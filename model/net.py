"""Defines the neural network, loss function and metrics"""

import numpy as np
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_scatter import scatter_add
from model.dynamic_reduction_network import DynamicReductionNetwork
from model.graph_met_network import GraphMETNetwork

'''
class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.drn = DynamicReductionNetwork(input_dim=11, hidden_dim=64,
                                           k = 8,
                                           output_dim=2, aggr='max',
                                           norm=torch.tensor([1./2950.0,         #px
                                                              1./2950.0,         #py
                                                              1./2950.0,         #pt
                                                              1./5.265625,       #eta
                                                              1./143.875,        #d0
                                                              1./589.,           #dz
                                                              1./1.2050781,      #mass
                                                              1./211.,           #pdgId
                                                              1.,                #charge
                                                              1./7.,             #fromPV
                                                              1.                 #puppiWeight
                                                          ]))
    def forward(self, data):
        output = self.drn(data)
        met    = nn.Softplus()(output[:,0]).unsqueeze(1)
        metphi = math.pi*(2*torch.sigmoid(output[:,1]) - 1).unsqueeze(1)
        output = torch.cat((met, metphi), 1)  
        return output
'''
class Net(nn.Module):
    def __init__(self, continuous_dim, categorical_dim, norm):
        super(Net, self).__init__()
        self.graphnet = GraphMETNetwork(continuous_dim, categorical_dim, norm,
                                        output_dim=1, hidden_dim=32,
                                        conv_depth=2)
    
    def forward(self, x_cont, x_cat, edge_index, batch):
        weights = self.graphnet(x_cont, x_cat, edge_index, batch)
        relu_layer = nn.ReLU() #ReLU weights
        return relu_layer(weights)
        # return torch.sigmoid(weights) #old sigmoid weights

def loss_fn_weighted(weights, prediction, truth, batch, sample_weight=None):
    # print('prediction:', prediction.shape)
    # print('truth', truth.shape)
    px=prediction[:,0]
    py=prediction[:,1]
    true_px=truth[:,0] 
    true_py=truth[:,1]
    # print('truepx:', true_px.shape, 'px:', px.shape)
    # print('truepy:', true_py.shape, 'py:', py.shape)
    #print('HT', truth[:,10])
    # print(weights.shape)
    # print(batch.shape)
    # print('batch:', batch)
    METx = scatter_add(weights*px, batch)
    METy = scatter_add(weights*py, batch)
    print('METx:', METx.shape)
    print('METy:', METy.shape)
    #tzero = torch.zeros(prediction.shape[0]).to('cuda')
    #BCE = nn.BCELoss()
    #prediction[:,]: pX,pY,pT,eta,d0,dz,mass,puppiWeight,pdgId,charge,fromPV
    # flatten out MET
    if sample_weight != None:
        binnings = [0, 20, 40, 60, 80, 100, 120, 140, 160, 180, 200, 220, 240, 260, 280, 300, 
                    320, 340, 360, 380, 400, 420, 440, 460, 480, 500, 550, 600, 650, 700, 1000, 4000]

        per_genMET_bin_weight = [0.93070056, 0.35927714, 0.28656409, 0.29433719, 0.32343765, 0.35436381,
                                0.45301355, 0.57090288, 0.70162305, 0.85617394, 0.98718005, 1.19053963,
                                1.44328697, 1.72151728, 2.02300314, 2.38527131, 2.60903403, 2.96740886,
                                3.37263112, 3.80113776, 4.10378135, 4.83129837, 5.50106654, 6.43845126,
                                7.30650132, 3.47393359, 4.34440619, 5.60510562, 6.80332763, 2.14469442,
                                4.26239942]
        # per_genMET_bin_weight = torch.tensor(per_genMET_bin_weight)

        v_true = torch.stack((true_px,true_py),dim=1)
        
        true_uT = getscale(v_true)

        for idx in range(len(binnings)-1):
            mask_uT = (true_uT > binnings[idx]) & (true_uT <= binnings[idx+1])
            sample_weight[mask_uT] = per_genMET_bin_weight[idx]            

        print(sample_weight)
        print(sample_weight.shape)
        print(type(sample_weight))
        # exit()

        loss=0.5*( ( ( METx + true_px)**2 + ( METy + true_py)**2 ) * sample_weight ).mean()
    else:
        loss=0.5*( ( METx + true_px)**2 + ( METy + true_py)**2 ).mean() 
    #+ 5000*BCE(torch.where(prediction[:,9]==0, tzero, weights), torch.where(prediction[:,9]==0, tzero, prediction[:,7]))
    return loss

def loss_fn_response_tune(weights, prediction, truth, batch, c = 350, scale_momentum = 128.):
    px=prediction[:,0]
    py=prediction[:,1]

    true_px = truth[:,0] / scale_momentum
    true_py = truth[:,1] / scale_momentum

    METx = scatter_add(weights*px, batch)
    METy = scatter_add(weights*py, batch)
    # predicted MET/qT
    print('METx:', METx)
    print('METy:', METy)
    print('true_px:', true_px)
    print('true_py:', true_py)

    v_true = torch.stack((true_px,true_py),dim=1)
    v_regressed = torch.stack((METx,METy),dim=1)
    response = getscale(v_regressed) / getscale(v_true)

    loss = 0.5*( (METx + true_px)**2 + (METy + true_py)**2 ).mean()

    pT_thres = 25./scale_momentum
    resp_pos = torch.logical_and(response > 1., getscale(v_true) > pT_thres)
    resp_neg = torch.logical_and(response < 1., getscale(v_true) > pT_thres)
    c = c / scale_momentum

    response_term = c * (torch.sum(1 - response[resp_neg]) + torch.sum(response[resp_pos] - 1))
    print('response_term:', response_term)

    loss += response_term
    return loss

def loss_fn(weights, prediction, truth, batch):
    px=prediction[:,0]
    py=prediction[:,1]

    true_px = truth[:,0]
    true_py = truth[:,1]

    METx = scatter_add(weights*px, batch)
    METy = scatter_add(weights*py, batch)
    # predicted MET/qT

    loss = 0.5*( (METx + true_px)**2 + (METy + true_py)**2 ).mean()
    
    return loss


def getdot(vx, vy):
    return torch.einsum('bi,bi->b',vx,vy)
def getscale(vx):
    return torch.sqrt(getdot(vx,vx))
def scalermul(a,v):
    return torch.einsum('b,bi->bi',a,v)

def u_perp_par_loss(weights, prediction, truth, batch):
    qTx=truth[:,0]#*torch.cos(truth[:,1])
    qTy=truth[:,0]#*torch.sin(truth[:,1])
    # truth qT
    v_qT=torch.stack((qTx,qTy),dim=1)

    px=prediction[:,0]
    py=prediction[:,1]
    METx = -scatter_add(weights*px, batch)
    METy = -scatter_add(weights*py, batch)
    # predicted MET/qT
    vector = torch.stack((METx, METy),dim=1)

    response = getdot(vector,v_qT)/getdot(v_qT,v_qT)
    v_paral_predict = scalermul(response, v_qT)
    u_paral_predict = getscale(v_paral_predict)-getscale(v_qT)
    v_perp_predict = vector - v_paral_predict
    u_perp_predict = getscale(v_perp_predict)
    
    return 0.5*(u_paral_predict**2 + u_perp_predict**2).mean()
    
def resolution(weights, prediction, truth, batch):
    
    def getdot(vx, vy):
        print('shapes:', vx.shape, vy.shape)
        print('vx:', vx)
        print('vy:', vy)
        return torch.einsum('bi,bi->b',vx,vy)
    def getscale(vx):
        return torch.sqrt(getdot(vx,vx))
    def scalermul(a,v):
        return torch.einsum('b,bi->bi',a,v)    

    qTx=truth[:,0]#*torch.cos(truth[:,1])
    qTy=truth[:,1]#*torch.sin(truth[:,1])
    # truth qT
    print('qTx:', qTx.shape)
    print('qTy:', qTy.shape)
    v_qT=torch.stack((qTx,qTy),dim=1)
    print('v_qT:', v_qT.shape)

    pfMETx=truth[:,2]#*torch.cos(truth[:,3])
    pfMETy=truth[:,3]#*torch.sin(truth[:,3])
    print('pfMETx:', pfMETx.shape)
    print('pfMETy:', pfMETy.shape)
    # PF MET
    v_pfMET=torch.stack((pfMETx, pfMETy),dim=1)
    print('v_pfMET:', v_pfMET.shape)

    puppiMETx=truth[:,4]#*torch.cos(truth[:,5])
    puppiMETy=truth[:,5]#*torch.sin(truth[:,5])
    # PF MET                                                                                                                                                            
    v_puppiMET=torch.stack((puppiMETx, puppiMETy),dim=1)

    has_deepmet = False
    if truth.size()[1] > 6:
        has_deepmet = True
        deepMETResponse_x=truth[:,6]#*torch.cos(truth[:,7])
        deepMETResponse_y=truth[:,7]#*torch.sin(truth[:,7])
        # DeepMET Response Tune
        v_deepMETResponse=torch.stack((deepMETResponse_x, deepMETResponse_y),dim=1)
    
        deepMETResolution_x=truth[:,8]#*torch.cos(truth[:,9])
        deepMETResolution_y=truth[:,9]#*torch.sin(truth[:,9])
        # DeepMET Resolution Tune
        v_deepMETResolution=torch.stack((deepMETResolution_x, deepMETResolution_y),dim=1)
    
    px=prediction[:,0]
    py=prediction[:,1]
    #weights = torch.where( prediction[:,9] == 10, weights , prediction[:,7] )
    METx = scatter_add(weights*px, batch)
    METy = scatter_add(weights*py, batch)
    # predicted MET/qT
    v_MET=torch.stack((METx, METy),dim=1)

    
    
    def compute(vector):
        
        response = getdot(vector,v_qT)/getdot(v_qT,v_qT)
        v_paral_predict = scalermul(response, v_qT)
        u_paral_predict = getscale(v_paral_predict)-getscale(v_qT)
        v_perp_predict = vector - v_paral_predict
        u_perp_predict = getscale(v_perp_predict)
        return [u_perp_predict.cpu().detach().numpy(), u_paral_predict.cpu().detach().numpy(), response.cpu().detach().numpy()]

    resolutions= {
        'MET':      compute(-v_MET),
        'pfMET':    compute(v_pfMET),
        'puppiMET': compute(v_puppiMET)
    }
    if has_deepmet:
        resolutions.update({
            'deepMETResponse':   compute(v_deepMETResponse),
            'deepMETResolution': compute(v_deepMETResolution)
        })
    return resolutions, torch.sqrt(truth[:,0]**2+truth[:,1]**2).cpu().detach().numpy()

# maintain all metrics required in this dictionary- these are used in the training and evaluation loops
metrics = {
    'resolution': resolution,
}
