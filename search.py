# External Imports
from datetime import datetime
import pprint
import signal
import sys
import torch
import torch.nn as nn
 
# Internal Imports
from config import SearchConfig
from learnt_model import LearntModel
from model_controller import ModelController
from operations import OPS, LEN_OPS
from torch.utils.tensorboard import SummaryWriter
from util import get_data, save_checkpoint, accuracy, AverageMeter, print_alpha
 
config = SearchConfig()
 
class HDARTS:
    def __init__(self):
        self.dt_string = datetime.now().strftime("%d-%m-%Y--%H-%M-%S")
        self.writer = SummaryWriter(config.LOGDIR + "/" + config.DATASET +  "/" + str(self.dt_string) + "/")
        self.num_levels = config.NUM_LEVELS

        # Set gpu device if cuda is available
        if torch.cuda.is_available():
            torch.cuda.set_device(config.gpus[0]) 

        # Write config to tensorboard
        hparams = {}
        for key in config.__dict__:
            if type(config.__dict__[key]) is dict or type(config.__dict__[key]) is list:
                hparams[key] = str(config.__dict__[key])
            else:
                hparams[key] = config.__dict__[key]
        
        # Print config to logs
        pprint.pprint(hparams)

        # Commented out for Hoffman Cluster
        # self.writer.add_hparams(hparams, {'accuracy': 0})

    def run(self):
        # Get Data & MetaData
        input_size, input_channels, num_classes, train_data = get_data(
            dataset_name=config.DATASET,
            data_path=config.DATAPATH,
            cutout_length=0,
            validation=False)
 
        # Set Loss Criterion
        loss_criterion = nn.CrossEntropyLoss()
        if torch.cuda.is_available():
            loss_criterion = loss_criterion.cuda()
        
        # Ensure num of ops at level 0 = num primitives
        config.NUM_OPS_AT_LEVEL[0] = LEN_OPS 
       
        # Initialize model
        self.model = ModelController(
            num_levels=config.NUM_LEVELS,
            num_nodes_at_level=config.NUM_NODES_AT_LEVEL,
            num_ops_at_level=config.NUM_OPS_AT_LEVEL,
            primitives=OPS,
            channels_in=input_channels,
            channels_start=config.CHANNELS_START,
            stem_multiplier=1,
            num_classes=num_classes,
            num_cells=config.NUM_CELLS,
            loss_criterion=loss_criterion,
            writer=self.writer
         )

        if torch.cuda.is_available():
            self.model = self.model.cuda()
 
        # Weights Optimizer
        w_optim = torch.optim.SGD(
            params=self.model.get_weights(),
            lr=config.WEIGHTS_LR,
            momentum=config.WEIGHTS_MOMENTUM,
            weight_decay=config.WEIGHTS_WEIGHT_DECAY)
        w_lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(w_optim, config.epochs, eta_min=config.WEIGHTS_LR_MIN)

        # Alpha Optimizer - one for each level
        alpha_optim = []
        for level in range(0, config.NUM_LEVELS):
            alpha_optim.append(torch.optim.Adam(
                    params=self.model.get_alpha_level(level),
                    lr=config.ALPHA_LR,
                    weight_decay=config.ALPHA_WEIGHT_DECAY,
                    beta=config.ALPHA_MOMENTUM))
 
 
        # Train / Validation Split
        n_train = (len(train_data) // 100) * config.PERCENTAGE_OF_DATA
        split = n_train // 2
        indices = list(range(n_train))
        train_sampler = torch.utils.data.sampler.SubsetRandomSampler(indices[:split])
        valid_sampler = torch.utils.data.sampler.SubsetRandomSampler(indices[split:])
        train_loader = torch.utils.data.DataLoader(train_data,
                                                batch_size=config.BATCH_SIZE,
                                                sampler=train_sampler,
                                                num_workers=config.NUM_DOWNLOAD_WORKERS,
                                                pin_memory=True)
        valid_loader = torch.utils.data.DataLoader(train_data,
                                                batch_size=config.BATCH_SIZE,
                                                sampler=valid_sampler,
                                                num_workers=config.NUM_DOWNLOAD_WORKERS,
                                                pin_memory=True)

        # Register Signal Handler for interrupts & kills
        signal.signal(signal.SIGINT, self.terminate)

        # Training Loop
        best_top1 = 0.
        for epoch in range(config.EPOCHS):
            w_lr_scheduler.step()
            lr = w_lr_scheduler.get_lr()[0]

            # Training
            self.train(
                train_loader=train_loader,
                valid_loader=valid_loader,
                model=self.model,
                w_optim=w_optim,
                alpha_optim=alpha_optim,
                epoch=epoch,
                lr=lr)

            # Validation
            cur_step = (epoch+1) * len(train_loader)
            top1 = self.validate(
                valid_loader=valid_loader,
                model=self.model,
                epoch=epoch,
                cur_step=cur_step)

            # Save Checkpoint
            if best_top1 < top1:
                best_top1 = top1
                is_best = True
            else:
                is_best = False
            print("Saving checkpoint")
            save_checkpoint(self.model, epoch, config.CHECKPOINT_PATH + "/" + self.dt_string, is_best)
 
        # Log Best Accuracy so far
        print("Final best Prec@1 = {:.4%}".format(best_top1))

        # Terminate
        self.terminate()
 
    def train(self, train_loader, valid_loader, model: ModelController, w_optim, alpha_optim, epoch, lr):
        top1 = AverageMeter()
        top5 = AverageMeter()
        losses = AverageMeter()

        cur_step = epoch*len(train_loader)
        
        # Log LR
        self.writer.add_scalar('train/lr', lr, epoch)

        # Prepares the model for training - 'training mode'
        model.train()

        for step, ((trn_X, trn_y), (val_X, val_y)) in enumerate(zip(train_loader, valid_loader)):
            N = trn_X.size(0)
            if torch.cuda.is_available():
                trn_X = trn_X.cuda()
                trn_y = trn_y.cuda()
                val_X = val_X.cuda()
                val_y = val_y.cuda()

            # Alpha Gradient Steps for each level
            for level in range(0, self.num_levels):
                alpha_optim[level].zero_grad()
                logits = model(val_X)
                loss = model.loss_criterion(logits, val_y)
                loss.backward()
                alpha_optim[level].step()

            # Weights Step
            w_optim.zero_grad()
            logits = model(trn_X)
            loss = model.loss_criterion(logits, trn_y)
            loss.backward()

            # gradient clipping
            nn.utils.clip_grad_norm_(model.get_weights(), config.WEIGHTS_GRADIENT_CLIP)
            w_optim.step()
 
            prec1, prec5 = accuracy(logits, trn_y, topk=(1, 5))
            losses.update(loss.item(), N)
            top1.update(prec1.item(), N)
            top5.update(prec5.item(), N)

            if step % config.PRINT_STEP_FREQUENCY == 0 or step == len(train_loader)-1:
                print(
                    datetime.now(),
                    "Train: [{:2d}/{}] Step {:03d}/{:03d} Loss {losses.avg:.3f} "
                    "Prec@(1,5) ({top1.avg:.1%}, {top5.avg:.1%})".format(
                       epoch+1, config.EPOCHS, step, len(train_loader)-1, losses=losses,
                        top1=top1, top5=top5))
 
            self.writer.add_scalar('train/loss', loss.item(), cur_step)
            self.writer.add_scalar('train/top1', prec1.item(), cur_step)
            self.writer.add_scalar('train/top5', prec5.item(), cur_step)
            cur_step += 1
 
        print("Train: [{:2d}/{}] Final Prec@1 {:.4%}".format(epoch+1, config.EPOCHS, top1.avg))
 
 
    def validate(self, valid_loader, model, epoch, cur_step):
        top1 = AverageMeter()
        top5 = AverageMeter()
        losses = AverageMeter()

        model.eval()

        with torch.no_grad():
            for step, (X, y) in enumerate(valid_loader):
                N = X.size(0)

                logits = model(X)
                
                if torch.cuda.is_available():
                    y = y.cuda()   

                loss = model.loss_criterion(logits, y)

                prec1, prec5 = accuracy(logits, y, topk=(1, 5))
                losses.update(loss.item(), N)
                top1.update(prec1.item(), N)
                top5.update(prec5.item(), N)
 
                if step % config.PRINT_STEP_FREQUENCY == 0 or step == len(valid_loader)-1:
                    print(
                        datetime.now(),
                        "Valid: [{:2d}/{}] Step {:03d}/{:03d} Loss {losses.avg:.3f} "
                        "Prec@(1,5) ({top1.avg:.1%}, {top5.avg:.1%})".format(
                            epoch+1, config.EPOCHS, step, len(valid_loader)-1, losses=losses,
                            top1=top1, top5=top5))
 
        self.writer.add_scalar('val/loss', losses.avg, cur_step)
        self.writer.add_scalar('val/top1', top1.avg, cur_step)
        self.writer.add_scalar('val/top5', top5.avg, cur_step)

        print("Valid: [{:2d}/{}] Final Prec@1 {:.4%}".format(epoch+1, config.EPOCHS, top1.avg))
 
        return top1.avg

    def terminate(self, signal=None, frame=None):
        # Print alpha
        print_alpha(self.model.alpha_normal, self.writer, "normal")
        print_alpha(self.model.alpha_reduce, self.writer, "reduce")
        
        '''
        # Ensure directories to save in exist
        learnt_model_path = config.LEARNT_MODEL_PATH
        if not os.path.exists(learnt_model_path):
            os.makedirs(learnt_model_path)

        # Save learnt model
        learnt_model = LearntModel(self.model.model)
        torch.save(learnt_model, learnt_model_path + "/" + self.dt_string + "_learnt_model")
        '''
        # Pass exit signal on
        sys.exit(0)

 
if __name__ == "__main__":
    if not torch.cuda.is_available():
        print('No GPU Available')
    nas = HDARTS()
    nas.run()
