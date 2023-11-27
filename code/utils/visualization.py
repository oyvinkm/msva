import os
from matplotlib import tri as mtri
from matplotlib import animation
import matplotlib.pyplot as plt
import numpy as np
import os 
from mpl_toolkits.axes_grid1 import make_axes_locatable
import torch
import copy
from torch import Tensor
import torch_geometric
from torch_geometric.utils import to_networkx
from torch_geometric.datasets import Planetoid
import networkx as nx
from networkx.algorithms import community
from loguru import logger

def save_plots(args, losses, test_losses, velo_val_losses):
    """Saves loss plots at args.postprocess_dir"""
    model_name='model_nl'+str(args.num_layers)+'_bs'+str(args.batch_size) + \
               '_hd'+str(args.hidden_dim)+'_ep'+str(args.epochs)+'_wd'+str(args.weight_decay) + \
               '_lr'+str(args.lr)+'_shuff_'+str(args.shuffle)+'_tr'+str(args.train_size)+'_te'+str(args.test_size)

    if not os.path.isdir(args.postprocess_dir):
        os.mkdir(args.postprocess_dir)

    PATH = os.path.join(args.postprocess_dir, model_name + '.pdf')

    f = plt.figure()
    plt.title('Losses Plot')
    plt.plot(losses, label="training loss" + " - " + args.model_type)
    plt.plot(test_losses, label="test loss" + " - " + args.model_type)
    plt.grid(true)
    plt.xlabel('Epoch')
    plt.ylabel('Loss')

    plt.legend()
    f.savefig(PATH, bbox_inches='tight')

def make_animation(gs, pred, evl, path, name , skip = 1, save_anim = True, plot_variables = False):
    '''
    input gs is a dataloader and each entry contains attributes of many timesteps.

    '''
    print('Generating velocity fields...')
    fig, axes = plt.subplots(3, 1, figsize=(20, 16))
    num_steps = len(gs) # for a single trajectory
    num_frames = num_steps // skip
    print(num_steps)
    def animate(num):
        step = (num*skip) % num_steps
        traj = 0

        # gt = next(gs)
        # diff = next(evl)
        # bb_min = gt.x.min()
        # bb_max = gt.x.max()
        # bb_min_evl = diff.x.min()
        # bb_max_evl = diff.x.max()

        bb_min = gs[0].x[:, 0:2].min() # first two columns are velocity
        bb_max = gs[0].x[:, 0:2].max() # use max and min velocity of gs dataset at the first step for both 
                                          # gs and prediction plots
        bb_min_evl = evl[0].x[:, 0:2].min()  # first two columns are velocity
        bb_max_evl = evl[0].x[:, 0:2].max()  # use max and min velocity of gs dataset at the first step for both 
                                          # gs and prediction plots
        count = 0

        for ax in axes:
            ax.cla()
            ax.set_aspect('equal')
            ax.set_axis_off()
            
            pos = gs[step].mesh_pos 
            faces = gs[step].cells
            if (count == 0):
                # ground truth
                velocity = gs[step].x[:, 0:2]
                title = 'Ground truth:'
            elif (count == 1):
                velocity = pred[step].x[:, 0:2]
                title = 'Reconstruction:'
            else: 
                velocity = evl[step].x[:, 0:2]
                title = 'Error: (Reconstruction - Ground truth)'

            triang = mtri.Triangulation(pos[:, 0], pos[:, 1], faces)
            if (count <= 1):
                # absolute values
                
                mesh_plot = ax.tripcolor(triang, velocity[:, 0], vmin= bb_min, vmax=bb_max,  shading='flat' ) # x-velocity
                ax.triplot(triang, 'ko-', ms=0.5, lw=0.3)
            else:
                # error: (pred - gs)/gs
                mesh_plot = ax.tripcolor(triang, velocity[:, 0], vmin= bb_min_evl, vmax=bb_max_evl, shading='flat' ) # x-velocity
                ax.triplot(triang, 'ko-', ms=0.5, lw=0.3)
                #ax.triplot(triang, lw=0.5, color='0.5')

            ax.set_title('{} Trajectory {} Step {}'.format(title, traj, step), fontsize = '20')
            #ax.color

            #if (count == 0):
            divider = make_axes_locatable(ax)
            cax = divider.append_axes('right', size='5%', pad=0.05)
            clb = fig.colorbar(mesh_plot, cax=cax, orientation='vertical')
            clb.ax.tick_params(labelsize=20) 
            
            clb.ax.set_title('x velocity (m/s)',
                             fontdict = {'fontsize': 20})
            count += 1
        return fig,

    # Save animation for visualization
    if not os.path.exists(path):
        os.makedirs(path)
    
    if (save_anim):
        gs_anim = animation.FuncAnimation(fig, animate, frames=num_frames, interval=1000)
        writergif = animation.PillowWriter(fps=10) 
        anim_path = os.path.join(path, '{}_anim.gif'.format(name))
        gs_anim.save( anim_path, writer=writergif)
        plt.show(block=True)
    else:
        pass

def draw_graph(g, save = False, args = None):
  """Draws the graph given"""
  G = to_networkx(g, to_undirected=True)
  pos = nx.spring_layout(G, seed=42)
  cent = nx.degree_centrality(G)
  node_size = list(map(lambda x: x * 500, cent.values()))
  cent_array = np.array(list(cent.values()))
  threshold = sorted(cent_array, reverse=True)[10]
  cent_bin = np.where(cent_array >= threshold, 1, 0.1)
  plt.figure(figsize=(12, 12))
  nodes = nx.draw_networkx_nodes(G, pos, node_size=node_size,
                                cmap=plt.cm.plasma,
                                node_color=cent_bin,
                                nodelist=list(cent.keys()),
                                alpha=cent_bin)
  edges = nx.draw_networkx_edges(G, pos, width=0.25, alpha=0.3)
  if save and args is not None:
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)
    plt.title(f'Graph num nodes: {args.num_nodes}')
    plt.savefig(os.path.join(args.save_dir, f'graph_{args.num_nodes}'))
  
  plt.show()

@torch.no_grad()
def plot_mesh(gs, title = None, args = None):
  """plots the graph as a mesh"""
  fig, ax = plt.subplots(1, 1, figsize=(20, 16))
  bb_min = gs.x[:, 0:2].min() # first two columns are velocity
  bb_max = gs.x[:, 0:2].max() # use max and min velocity of gs dataset at the first step for both 
                                    # gs and prediction plots


  ax.cla()
  ax.set_aspect('equal')
  ax.set_axis_off()

  pos = gs.mesh_pos
  faces = gs.cells
  velocity = gs.x[:, 0:2]


  triang = mtri.Triangulation(pos[:, 0].cpu(), pos[:, 1].cpu(), faces.cpu())
  mesh_plot = ax.tripcolor(triang, velocity[:, 0].cpu(), vmin= bb_min, vmax=bb_max,  shading='flat' ) # x-velocity
  ax.triplot(triang, 'ko-', ms=0.5, lw=0.3)


  ax.set_title(title, fontsize = '20')
  #ax.color

  #if (count == 0):
  divider = make_axes_locatable(ax)
  cax = divider.append_axes('right', size='5%', pad=0.05)
  clb = fig.colorbar(mesh_plot, cax=cax, orientation='vertical')
  clb.ax.tick_params(labelsize=20) 

  clb.ax.set_title('x velocity (m/s)',
                      fontdict = {'fontsize': 20})
  return fig


@torch.no_grad()
def plot_dual_mesh(pred_gs, true_gs, title = None, args = None):
    """
    Plots two graphs with each other. 
    Can be used to plot the predicted graph and the ground truth
    """
    fig, axes = plt.subplots(2, 1, figsize=(20, 16))
    bb_min = true_gs.x[:, 0:2].min() # first two columns are velocity
    bb_max = true_gs.x[:, 0:2].max() # use max and min velocity of gs dataset at the first step for both 
                                        # gs and prediction plots

    for idx, ax in enumerate(axes):
        if idx == 0:
            pos = pred_gs.mesh_pos
            faces = pred_gs.cells
            velocity = pred_gs.x[:, 0:2]
            bb_min = pred_gs.x[:, 0:2].min() # first two columns are velocity
            bb_max = pred_gs.x[:, 0:2].max() # use max and min velocity of gs dataset at the first step for both 
                                        # gs and prediction plots
            title = 'Reconstruction'
        elif idx == 1:
            pos = true_gs.mesh_pos
            faces = true_gs.cells
            velocity = true_gs.x[:, 0:2]
            bb_min = true_gs.x[:, 0:2].min() # first two columns are velocity
            bb_max = true_gs.x[:, 0:2].max() # use max and min velocity of gs dataset at the first step for both 
                                        # gs and prediction plots
            title = 'Ground Truth'

        ax.cla()
        ax.set_aspect('equal')
        ax.set_axis_off()

        


        triang = mtri.Triangulation(pos[:, 0].cpu(), pos[:, 1].cpu(), faces.cpu())
        mesh_plot = ax.tripcolor(triang, velocity[:, 0].cpu(), vmin= bb_min, vmax=bb_max,  shading='flat' ) # x-velocity
        ax.triplot(triang, 'ko-', ms=0.5, lw=0.3)


        ax.set_title(title, fontsize = '20')
        #ax.color

        #if (count == 0):
        divider = make_axes_locatable(ax)
        cax = divider.append_axes('right', size='5%', pad=0.05)
        clb = fig.colorbar(mesh_plot, cax=cax, orientation='vertical')
        clb.ax.tick_params(labelsize=20) 

        clb.ax.set_title('x velocity (m/s)',
                            fontdict = {'fontsize': 20})
    return fig


def plot_loss(train_loss=None, train_label = 'Rotate', 
                   val_loss=None, val_label = 'One or Two', 
                   extra_loss=None, extra_label = 'Patches', 
                   label="Loss", title = 'Loss / Epoch', PATH = None):
    """
    Takes a list of training and/or validation metrics and plots them
    Returns: plt.figure and ax objects
    """
    if train_loss is None and val_loss is None:
        raise ValueError("Must specify at least one of 'train_histories' and 'val_histories'")
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111)
    
    epochs = np.arange(len(train_loss))
    if train_loss is not None:
        ax.plot(epochs, train_loss, linewidth = .8, label=train_label, color="dodgerblue")
    if val_loss is not None:
        ax.plot(epochs, val_loss, linewidth = .8, label=val_label, color="darkgreen")
    if extra_loss is not None:
        ax.plot(epochs, extra_loss, linewidth = .8, label=extra_label, color="darkred")
    ax.set_xlabel("Epoch")
    ax.set_ylabel(label)
    ax.legend(loc=0)
    ax.grid(True)
    fig.suptitle(title)
    if PATH is not None:
        plt.savefig(PATH)
    
    return fig, ax