from pathlib import Path
import json
import warnings
from ._model_extension import ModelExtension
from ._arcgis_model import _EmptyData

try:
    from fastai.vision import flatten_model, ImageList 
    from fastai.vision import imagenet_stats, normalize
    import torch
    from fastai.torch_core import split_model_idx
    from .._utils.pascal_voc_rectangles import ObjectDetectionCategoryList
    from .._utils.common import get_multispectral_data_params_from_emd, _get_emd_path
    from ._arcgis_model import _resnet_family, _get_device

    HAS_FASTAI = True

except Exception as e:
    HAS_FASTAI = False

class MyFasterRCNN():
    """
    Create class with following fixed function names and the number of arguents to train your model from external source
    """

    try:
        import torch
        import torchvision
        import pathlib
        import os
        import fastai
    except:
        pass
    
    def get_model(self, data, backbone=None, **kwargs):
        """
        In this fuction you have to define your model with following two arguments!
        
        data - Object returned from prepare_data method(Fastai databunch)
        
        These two arguments comes from dataset which you have prepared from prepare_data method above.
        
        """
        self.fasterrcnn_kwargs, kwargs = self.fastai.core.split_kwargs_by_func(kwargs,
                                                                         self.torchvision.models.detection.FasterRCNN.__init__)
        if backbone is None:
            backbone = self.torchvision.models.resnet50

        elif type(backbone) is str:
            if hasattr(self.torchvision.models, backbone):
                backbone = getattr(self.torchvision.models, backbone)
            elif hasattr(self.torchvision.models.detection, backbone):
                backbone = getattr(self.torchvision.models.detection, backbone)
        else:
            backbone = backbone
        pretrained_backbone = kwargs.get('pretrained_backbone', True)
        assert type(pretrained_backbone) == bool
        if backbone.__name__ is 'resnet50':
            model = self.torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=pretrained_backbone,
                                                                              min_size = 1.5*data.chip_size,
                                                                              max_size = 2*data.chip_size,
                                                                              **self.fasterrcnn_kwargs)
        elif backbone.__name__ in ['resnet18','resnet34']:
            backbone_small = self.fastai.vision.learner.create_body(backbone, pretrained=pretrained_backbone)
            backbone_small.out_channels = 512
            model = self.torchvision.models.detection.FasterRCNN(backbone_small,
                                                                 91,
                                                                 min_size = 1.5*data.chip_size,
                                                                 max_size = 2*data.chip_size,
                                                                 **self.fasterrcnn_kwargs)
        else:
            backbone_fpn = self.torchvision.models.detection.backbone_utils.resnet_fpn_backbone(
                backbone.__name__,
                pretrained = pretrained_backbone
            )
            model = self.torchvision.models.detection.FasterRCNN(backbone_fpn,
                                                                 91,
                                                                 min_size = 1.5*data.chip_size,
                                                                 max_size = 2*data.chip_size,
                                                                 **self.fasterrcnn_kwargs)

        in_features = model.roi_heads.box_predictor.cls_score.in_features
        model.roi_heads.box_predictor = self.torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, len(data.classes))
        
        if data._is_multispectral:
            model.transform.image_mean = [0]*len(data._extract_bands)
            model.transform.image_std = [1]*len(data._extract_bands)

        self.model = model

        return model
    
    def on_batch_begin(self, learn, model_input_batch, model_target_batch):
        """
        This fuction is dedicated to put the inputs and outputs of the model before training. This is equivalent to fastai
        on_batch_begin function. In this function you will get the inputs and targets with applied transormations. You should
        be very carefull to return the model input and target during traing, model will only accept model_input(in many cases it
        is possible to model accept input and target both to return the loss during traing and you don't require to compute loss
        from the model output and the target by yourself), if you want to compute the loss by yourself by taking the output of the
        model and targets then you have to return the model_target in desired format to calculate loss in the loss function.
        
        learn - Fastai learner object.                
        model_input_batch - transformed input batch(images) with tensor shape [N,C,H,W].        
        model_target_batch - transformed target batch. list with [bboxes, classes]. Where bboxes tensor shape will be
                            [N, maximum_num_of_boxes_pesent_in_one_image_of_the_batch, 4(y1,x1,y2,x2 fastai default bbox
                            formate)] and bboxes in the range from -1 to 1(default fastai formate), and classes is the tenosr
                            of shape [N, maximum_num_of_boxes_pesent_in_one_image_of_the_batch] which represents class of each
                            bboxes.
        if you are synthesizing new data from the model_target_batch and model_input_batch, in that case you need to put 
        your data on correct device.

        return model_input and model_target from this function.
        
        """
        
        #during training after each epoch, validation loss is required on validation set of datset.
        #torchvision FasterRCNN model gives losses only on training mode that is why set your model in train mode
        #such that you can get losses for your validation datset as well after each epoch.
        learn.model.train()

        target_list = []

        #denormalize from imagenet_stats
        if not learn.data._is_multispectral:
            imagenet_stats = [[0.485, 0.456, 0.406], [0.229, 0.224, 0.225]]
            mean = self.torch.tensor(imagenet_stats[0], dtype=self.torch.float32).to(model_input_batch.device)
            std  = self.torch.tensor(imagenet_stats[1], dtype=self.torch.float32).to(model_input_batch.device)
            model_input_batch = (model_input_batch.permute(0, 2, 3, 1)*std + mean).permute(0, 3, 1, 2)
        
        for bbox, label in zip(*model_target_batch):

            bbox = ((bbox+1)/2)*learn.data.chip_size # FasterRCNN model require bboxes with values between 0 and H and 0 and W.
            target = {}#FasterRCNN require target of each image in the formate of dictionary.
            #If image comes without any bboxes.
            if bbox.nelement() == 0:        
                bbox = self.torch.tensor([[0.,0.,0.,0.]]).to(learn.data.device)
                label = self.torch.tensor([0]).to(learn.data.device)
            # FasterRCNN require the formate of bboxes [x1,y1,x2,y2].
            bbox = self.torch.index_select(bbox, 1, self.torch.tensor([1,0,3,2]).to(learn.data.device))
            target["boxes"] = bbox
            target["labels"] = label
            target_list.append(target) #FasterRCNN require batches target in form of list of dictionary.
        
        #FasterRCNN require model input with images and coresponding targets in training mode to return the losses so append
        #the targets in model input itself.
        model_input = [list(model_input_batch), target_list]
        #Model target is not required in traing mode so just return the same model_target to train the model.
        model_target = model_target_batch

        #return model_input and model_target
        return model_input, model_target
    
    def transform_input(self, xb, thresh=0.5, nms_overlap=0.1):# transform_input
        """
        function for feding the input to the model in validation/infrencing mode.
        
        xb - tensor with shape [N, C, H, W]
        """
        self.nms_thres = self.model.roi_heads.nms_thresh
        self.thresh = self.model.roi_heads.score_thresh
        self.model.roi_heads.nms_thresh = nms_overlap
        self.model.roi_heads.score_thresh = thresh

        #denormalize from imagenet_stats
        imagenet_stats = [[0.485, 0.456, 0.406], [0.229, 0.224, 0.225]]
        mean = self.torch.tensor(imagenet_stats[0], dtype=self.torch.float32).to(xb.device)
        std  = self.torch.tensor(imagenet_stats[1], dtype=self.torch.float32).to(xb.device)

        xb = (xb.permute(0, 2, 3, 1)*std + mean).permute(0, 3, 1, 2)
        
        return list(xb) # model input require in the formate of list
    
    def transform_input_multispectral(self, xb, thresh=0.5, nms_overlap=0.1):

        self.nms_thres = self.model.roi_heads.nms_thresh
        self.thresh = self.model.roi_heads.score_thresh
        self.model.roi_heads.nms_thresh = nms_overlap
        self.model.roi_heads.score_thresh = thresh

        return list(xb)

    def loss(self, model_output, *model_target):
        """
        Define loss in this function.
        
        model_output - model output after feding input to the model in traing mode.
        *model_target - targets of the model which you have return in above on_batch_begin function.
        
        return loss for the model
        """
        #FasterRCNN model return loss in traing mode by feding input to the model it does not require target to compute the loss
        final_loss = 0.
        for i in model_output.values():
            i[self.torch.isnan(i)] = 0.
            i[self.torch.isinf(i)] = 0.
            final_loss += i
        
        return final_loss
    
    def post_process(self, pred, nms_overlap, thres, chip_size, device):
        """
        Fuction dedicated for post processing your output of the model in validation/infrencing mode.
        
        pred - Predictions(output) of the model after feding the batch of input image.
        nms_overlap - If your model post processing require nms_overlap.
        thres - detction thresold if required in post processing.
        chip_size - If chip_size required in model post processing.
        device - device on which you should put you output after post processing.
        
        It should return the bboxes in range -1 to 1 and the formate of the post processed result is list of tuple for each
        image and tuple should contain (bboxes, label, score) for each image. bboxes should be the tensor of shape
        [Number_of_bboxes_in_image, 4], label should be the tensor of shape[Number_of_bboxes_in_image,] and score should be
        the tensor of shape[Number_of_bboxes_in_image,].
        """

        self.model.roi_heads.score_thresh = self.thresh
        self.model.roi_heads.nms_thresh = self.nms_thres

        post_processed_pred = []
        for p in pred:
            
            bbox, label, score = p["boxes"], p["labels"], p["scores"]
            #convert bboxes in range -1 to 1.
            bbox = bbox/(chip_size/2) - 1
            #convert bboxes in format [y1,x1,y2,x2]
            bbox = self.torch.index_select(bbox, 1, self.torch.tensor([1,0,3,2]).to(bbox.device))
            #Append the tuple in list for each image
            post_processed_pred.append((bbox.data.to(device), label.to(device), score.to(device)))
            
        return post_processed_pred

class FasterRCNN(ModelExtension):
    """
    Model architecture from https://arxiv.org/abs/1506.01497.
    Creates a ``FasterRCNN`` object detection model,
    based on https://github.com/pytorch/vision/blob/master/torchvision/models/detection/faster_rcnn.py.

    =============================   =============================================
    **Argument**                    **Description**
    -----------------------------   ---------------------------------------------
    data                            Required fastai Databunch. Returned data object from
                                    ``prepare_data`` function.
    -----------------------------   ---------------------------------------------
    backbone                        Optional function. Backbone CNN model to be used for
                                    creating the base of the `FasterRCNN`, which
                                    is `resnet50` by default. 
                                    Compatible backbones: 'resnet18', 'resnet34', 
                                    'resnet50', 'resnet101', 'resnet152'
    -----------------------------   ---------------------------------------------
    pretrained_path                 Optional string. Path where pre-trained model is
                                    saved.
    =============================   =============================================

    **kwargs**

    =============================   =============================================
    **Argument**                    **Description**
    -----------------------------   ---------------------------------------------
    rpn_pre_nms_top_n_train         Optional int. Number of proposals to keep before
                                    applying NMS during training.
                                    Default: 2000
    -----------------------------   ---------------------------------------------
    rpn_pre_nms_top_n_test          Optional int. Number of proposals to keep before
                                    applying NMS during testing.
                                    Default: 1000
    -----------------------------   ---------------------------------------------
    rpn_post_nms_top_n_train        Optional int. Number of proposals to keep after
                                    applying NMS during training.
                                    Default: 2000
    -----------------------------   ---------------------------------------------
    rpn_post_nms_top_n_test         Optional int. Number of proposals to keep after
                                    applying NMS during testing.
                                    Default: 1000
    -----------------------------   ---------------------------------------------
    rpn_nms_thresh                  Optional float. NMS threshold used for postprocessing
                                    the RPN proposals.
                                    Default: 0.7
    -----------------------------   ---------------------------------------------
    rpn_fg_iou_thresh               Optional float. Minimum IoU between the anchor
                                    and the GT box so that they can be considered
                                    as positive during training of the RPN.
                                    Default: 0.7
    -----------------------------   ---------------------------------------------
    rpn_bg_iou_thresh               Optional float. Maximum IoU between the anchor and
                                    the GT box so that they can be considered as negative
                                    during training of the RPN.
                                    Default: 0.3
    -----------------------------   ---------------------------------------------
    rpn_batch_size_per_image        Optional int. Number of anchors that are sampled
                                    during training of the RPN for computing the loss.
                                    Default: 256
    -----------------------------   ---------------------------------------------
    rpn_positive_fraction           Optional float. Proportion of positive anchors in a
                                    mini-batch during training of the RPN.
                                    Default: 0.5
    -----------------------------   ---------------------------------------------
    box_score_thresh                Optional float. During inference, only return proposals
                                    with a classification score greater than box_score_thresh
                                    Default: 0.05
    -----------------------------   ---------------------------------------------
    box_nms_thresh                  Optional float. NMS threshold for the prediction head.
                                    Used during inference.
                                    Default: 0.5
    -----------------------------   ---------------------------------------------
    box_detections_per_img          Optional int. Maximum number of detections per
                                    image, for all classes.
                                    Default: 100
    -----------------------------   ---------------------------------------------
    box_fg_iou_thresh               Optional float. Minimum IoU between the proposals and
                                    the GT box so that they can be considered as positive
                                    during training of the classification head.
                                    Default: 0.5
    -----------------------------   ---------------------------------------------
    box_bg_iou_thresh               Optional float. Maximum IoU between the proposals and 
                                    the GT box so that they can be considered as negative 
                                    during training of the classification head.
                                    Default: 0.5
    -----------------------------   ---------------------------------------------
    box_batch_size_per_image        Optional int. Number of proposals that are sampled during
                                    training of the classification head.
                                    Default: 512
    -----------------------------   ---------------------------------------------
    box_positive_fraction           Optional float. Proportion of positive proposals in a
                                    mini-batch during training of the classification head.
                                    Default: 0.25
    =============================   =============================================

    :returns: ``FasterRCNN`` Object
    """
    def __init__(self, data, backbone='resnet50', pretrained_path=None, **kwargs):

        self._check_dataset_support(data)
        backbone_name = backbone if type(backbone) is str else backbone.__name__
        if backbone_name not in self.supported_backbones:
            raise Exception (f"Enter only compatible backbones from {', '.join(self.supported_backbones)}")

        super().__init__(data, MyFasterRCNN, backbone, pretrained_path, **kwargs)

        idx = 27
        if self._backbone.__name__ in ['resnet18','resnet34']:
            idx = self._freeze()
        self.learn.layer_groups = split_model_idx(self.learn.model, [idx])
        self.learn.create_opt(lr=3e-3)

    def unfreeze(self):
        for _, param in self.learn.model.named_parameters():
            param.requires_grad = True

    def _freeze(self):
        "Freezes the pretrained backbone."
        for idx, i in enumerate(flatten_model(self.learn.model.backbone)):
            if isinstance(i, (torch.nn.BatchNorm2d)):
                continue
            for p in i.parameters():
                p.requires_grad = False
        return idx

    @property
    def _is_fasterrcnn(self):
        return True

    @property
    def supported_backbones(self):
        """ Supported torchvision backbones for this model. """
        return FasterRCNN._supported_backbones()

    @staticmethod
    def _supported_backbones():
        return [*_resnet_family]

    @property
    def  supported_datasets(self):
        """ Supported dataset types for this model. """
        return FasterRCNN._supported_datasets()
    
    @staticmethod
    def _supported_datasets():
        return ['PASCAL_VOC_rectangles', 'KITTI_rectangles']

    @classmethod
    def from_model(cls, emd_path, data=None):
        """
        Creates a ``FasterRCNN`` object from an Esri Model Definition (EMD) file.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        emd_path                Required string. Path to Deep Learning Package
                                (DLPK) or Esri Model Definition(EMD) file.
        ---------------------   -------------------------------------------
        data                    Required fastai Databunch or None. Returned data
                                object from ``prepare_data`` function or None for
                                inferencing.

        =====================   ===========================================

        :returns: `FasterRCNN` Object
        """
        emd_path = _get_emd_path(emd_path)

        with open(emd_path) as f:
            emd = json.load(f)
            
        model_file = Path(emd['ModelFile'])
        
        if not model_file.is_absolute():
            model_file = emd_path.parent / model_file
        
        backbone = emd['ModelParameters']['backbone']
        dataset_type = emd.get('DatasetType', 'PASCAL_VOC_rectangles')
        chip_size = emd["ImageWidth"]
        resize_to = emd.get('resize_to', None)
        kwargs = emd.get('Kwargs', {})
        if isinstance(resize_to, list):
            resize_to = (resize_to[0], resize_to[1])

        try:
            class_mapping = {i['Value'] : i['Name'] for i in emd['Classes']}
            color_mapping = {i['Value'] : i['Color'] for i in emd['Classes']}
        except KeyError:
            class_mapping = {i['ClassValue'] : i['ClassName'] for i in emd['Classes']} 
            color_mapping = {i['ClassValue'] : i['Color'] for i in emd['Classes']}                

        data_passed = True
        if data is None:
            
            data_passed = False
            train_tfms = []
            val_tfms = []
            ds_tfms = (train_tfms, val_tfms)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                sd = ImageList([], path=emd_path.parent.parent.parent).split_by_idx([])
                data = sd.label_const(0, label_cls=ObjectDetectionCategoryList, classes=list(class_mapping.values())).transform(ds_tfms).databunch(device=_get_device()).normalize(imagenet_stats)
            # Add 1 for background class
            data.c += 1
            data.chip_size = chip_size
            data.class_mapping = class_mapping
            data.color_mapping = color_mapping
            data.classes = ['background'] + list(class_mapping.values())
            data._is_empty = True
            data.emd_path = emd_path
            data.emd = emd
            data = get_multispectral_data_params_from_emd(data, emd)
            data.dataset_type = dataset_type

        data.resize_to = resize_to
        frcnn = cls(data, backbone, pretrained_path=str(model_file), **kwargs)

        if not data_passed:
            frcnn.learn.data.single_ds.classes = frcnn._data.classes
            frcnn.learn.data.single_ds.y.classes = frcnn._data.classes
        
        return frcnn

    def predict(
        self,
        image_path,
        threshold=0.5,
        nms_overlap=0.1,
        return_scores=False,
        visualize=False,
        resize=False
    ):

        """
        Runs prediction on an Image.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        image_path              Required. Path to the image file to make the
                                predictions on.
        ---------------------   -------------------------------------------
        threshold               Optional float. The probability above which
                                a detection will be considered valid.
        ---------------------   -------------------------------------------
        nms_overlap             Optional float. The intersection over union
                                threshold with other predicted bounding
                                boxes, above which the box with the highest
                                score will be considered a true positive.
        ---------------------   -------------------------------------------
        return_scores           Optional boolean. Will return the probability
                                scores of the bounding box predictions if True.
        ---------------------   -------------------------------------------
        visualize               Optional boolean. Displays the image with
                                predicted bounding boxes if True.
        ---------------------   -------------------------------------------
        resize                  Optional boolean. Resizes the image to the same size
                                (chip_size parameter in prepare_data) that the model was trained on,
                                before detecting objects.
                                Note that if resize_to parameter was used in prepare_data,
                                the image is resized to that size instead.

                                By default, this parameter is false and the detections are run
                                in a sliding window fashion by applying the model on cropped sections
                                of the image (of the same size as the model was trained on).
        =====================   ===========================================
        
        :returns: Returns a tuple with predictions, labels and optionally confidence scores
                  if return_scores=True. The predicted bounding boxes are returned as a list
                  of lists containing the  xmin, ymin, width and height of each predicted object
                  in each image. The labels are returned as a list of class values and the
                  confidence scores are returned as a list of floats indicating the confidence
                  of each prediction.
        """

    def predict_video(
            self,
            input_video_path,
            metadata_file,
            threshold=0.5,
            nms_overlap=0.1,
            track=False,
            visualize=False,
            output_file_path=None,
            multiplex=False,
            multiplex_file_path=None,
            tracker_options={
                'assignment_iou_thrd': 0.3,
                'vanish_frames': 40,
                'detect_frames': 10
            },
            visual_options={
                'show_scores': True,
                'show_labels': True,
                'thickness': 2,
                'fontface': 0,
                'color': (255, 255, 255)
            },
            resize=False
        ):

            """
            Runs prediction on a video and appends the output VMTI predictions in the metadata file.

            =====================   ===========================================
            **Argument**            **Description**
            ---------------------   -------------------------------------------
            input_video_path        Required. Path to the video file to make the
                                    predictions on.
            ---------------------   -------------------------------------------
            metadata_file           Required. Path to the metadata csv file where
                                    the predictions will be saved in VMTI format.
            ---------------------   -------------------------------------------
            threshold               Optional float. The probability above which
                                    a detection will be considered.
            ---------------------   -------------------------------------------
            nms_overlap             Optional float. The intersection over union
                                    threshold with other predicted bounding
                                    boxes, above which the box with the highest
                                    score will be considered a true positive.
            ---------------------   -------------------------------------------
            track                   Optional bool. Set this parameter as True to
                                    enable object tracking. 
            ---------------------   -------------------------------------------
            visualize               Optional boolean. If True a video is saved
                                    with prediction results.
            ---------------------   -------------------------------------------
            output_file_path        Optional path. Path of the final video to be saved.
                                    If not supplied, video will be saved at path input_video_path
                                    appended with _prediction.
            ---------------------   -------------------------------------------
            multiplex               Optional boolean. Runs Multiplex using the VMTI detections.
            ---------------------   -------------------------------------------
            multiplex_file_path     Optional path. Path of the multiplexed video to be saved.
                                    By default a new file with _multiplex.MOV extension is saved
                                    in the same folder.
            ---------------------   -------------------------------------------
            tracking_options        Optional dictionary. Set different parameters for
                                    object tracking. assignment_iou_thrd parameter is used
                                    to assign threshold for assignment of trackers, 
                                    vanish_frames is the number of frames the object should
                                    be absent to consider it as vanished, detect_frames 
                                    is the number of frames an object should be detected
                                    to track it.
            ---------------------   -------------------------------------------
            visual_options          Optional dictionary. Set different parameters for
                                    visualization.
                                    show_scores boolean, to view scores on predictions,
                                    show_labels boolean, to view labels on predictions,
                                    thickness integer, to set the thickness level of box,
                                    fontface integer, fontface value from opencv values,
                                    color tuple (B, G, R), tuple containing values between
                                    0-255.
            ---------------------   -------------------------------------------
            resize                  Optional boolean. Resizes the video frames to the same size
                                    (chip_size parameter in prepare_data) that the model was trained on,
                                    before detecting objects.
                                    Note that if resize_to parameter was used in prepare_data,
                                    the video frames are resized to that size instead.

                                    By default, this parameter is false and the detections are run
                                    in a sliding window fashion by applying the model on cropped sections
                                    of the frame (of the same size as the model was trained on).
            =====================   ===========================================
            
            """

    def average_precision_score(self, detect_thresh=0.2, iou_thresh=0.1, mean=False, show_progress=True):

        """
        Computes average precision on the validation set for each class.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        detect_thresh           Optional float. The probability above which
                                a detection will be considered for computing
                                average precision.
        ---------------------   -------------------------------------------
        iou_thresh              Optional float. The intersection over union
                                threshold with the ground truth labels, above
                                which a predicted bounding box will be
                                considered a true positive.
        ---------------------   -------------------------------------------
        mean                    Optional bool. If False returns class-wise
                                average precision otherwise returns mean
                                average precision.                        
        =====================   ===========================================
        
        :returns: `dict` if mean is False otherwise `float`
        """

    def show_results(self, rows=5, thresh=0.5, nms_overlap=0.1):

        """
        Displays the results of a trained model on a part of the validation set.
        """