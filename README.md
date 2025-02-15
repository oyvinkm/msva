# SST_GNN
Master thesis studying self supervised temporal graph neural networks.

# GIF
GIF made by embedding a dataset of unseen data into latent vectors of size 128 and decoding these to graphs again

![Alt Text](https://github.com/oyvinkm/SST_GNN/blob/main/code/the2.gif)

## Flow of extrapolator/intrapolator:
![Alt Text](https://github.com/oyvinkm/SST_GNN/blob/main/code/Deformator.png)

## Extrapolating using feedback to predict future
![Alt Text](https://github.com/oyvinkm/SST_GNN/blob/main/code/feedback_gif.gif)

## Extrapolating one_step at the time $$\tilde{z}_{t+2} \leftarrow f (z_t, z_t+1)$$
![Alt Text](https://github.com/oyvinkm/SST_GNN/blob/main/code/one_step_gif.gif)
