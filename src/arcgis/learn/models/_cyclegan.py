from ._codetemplate import image_translation_prf
import json
import traceback
from .._data import _raise_fastai_import_error
from ._arcgis_model import ArcGISModel
import logging
logger = logging.getLogger()
try:
    from ._cyclegan_utils import CycleGanLoss, CycleGANTrainer, optim, compute_fid_metric
    from ._cyclegan_utils import  CycleGAN as CycleGAN_model
    from .._utils.cyclegan import ImageTuple, ImageTupleList, ImageTupleListMS
    from .._utils.common import get_multispectral_data_params_from_emd, _get_emd_path, ArcGISMSImage
    from torchvision import transforms
    from pathlib import Path
    from fastai.vision import DatasetType, Learner, partial, open_image, Image
    import torch
    from .._utils.env import _IS_ARCGISPRONOTEBOOK

    HAS_FASTAI = True
except Exception as e:
    import_exception = "\n".join(traceback.format_exception(type(e), e, e.__traceback__))
    HAS_FASTAI = False

class CycleGAN(ArcGISModel):

    """
    Creates a model object which generates images of type A from type B or type B from type A.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    data                    Required fastai Databunch. Returned data object from
                            `prepare_data` function.
    ---------------------   -------------------------------------------
    pretrained_path         Optional string. Path where pre-trained model is
                            saved.
    ---------------------   -------------------------------------------
    gen_blocks              Optional integer. Number of ResNet blocks to use 
                            in generator.
    ---------------------   -------------------------------------------
    lsgan                   Optional boolean. If True, it will use Mean Squared Error
                            else it will use Binary Cross Entropy.
    =====================   ===========================================
                                             
    :returns: `CycleGAN` Object
    """
    def __init__(self, data, pretrained_path=None, gen_blocks=9, lsgan=True, *args, **kwargs):
        super().__init__(data)
        self._check_dataset_support(data)
        cycle_gan = CycleGAN_model(self._data.n_channel,self._data.n_channel, gen_blocks=gen_blocks, lsgan=lsgan)
        self.learn = Learner(data, cycle_gan, loss_func=CycleGanLoss(cycle_gan), opt_func=partial(optim.Adam, betas=(0.5,0.99)),callback_fns=[CycleGANTrainer])
        self.learn.model = self.learn.model.to(self._device)
        self._slice_lr = False
        if pretrained_path is not None:
            self.load(pretrained_path)
        self._code = image_translation_prf
        def __str__(self):
            return self.__repr__()
        def __repr__(self):
            return '<%s>' % (type(self).__name__)

    @classmethod
    def from_model(cls, emd_path, data=None):
        """
        Creates a CycleGAN object from an Esri Model Definition (EMD) file.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        data                    Required fastai Databunch or None. Returned data
                                object from `prepare_data` function or None for
                                inferencing.
        ---------------------   -------------------------------------------
        emd_path                Required string. Path to Deep Learning Package
                                (DLPK) or Esri Model Definition(EMD) file.
        =====================   ===========================================
        
        :returns: `CycleGAN` Object
        """

        if not HAS_FASTAI:
            _raise_fastai_import_error(import_exception=import_exception)
            
        emd_path = _get_emd_path(emd_path)
        with open(emd_path) as f:
            emd = json.load(f)

        model_file = Path(emd['ModelFile'])

        if not model_file.is_absolute():
            model_file = emd_path.parent / model_file

        model_params = emd['ModelParameters']
        resize_to = emd.get('resize_to')
        chip_size = emd['ImageHeight']
        if data is None:
            if emd.get('IsMultispectral', False):
                data = ImageTupleListMS.from_folders(emd_path.parent, emd_path.parent, emd_path.parent, batch_stats_a=None, batch_stats_b=None).split_none().label_empty().databunch(bs=2, no_check=True)
                data = get_multispectral_data_params_from_emd(data, emd)
                data._is_multispectral = emd.get('IsMultispectral', False)
                normalization_stats_b = dict(emd.get("NormalizationStats_b"))
                for _stat in normalization_stats_b:
                    if normalization_stats_b[_stat] is not None:
                        normalization_stats_b[_stat] = torch.tensor(normalization_stats_b[_stat])
                    setattr(data, ('_'+_stat), normalization_stats_b[_stat])

            else:
                data = ImageTupleList.from_folders(emd_path.parent, emd_path.parent, emd_path.parent)\
                    .split_none()\
                    .label_empty()\
                    .transform(size=(resize_to, resize_to))\
                    .databunch(bs=2, no_check=True)

            data.n_channel = emd['n_channel']
            data._is_empty = True
            data.emd_path = emd_path
            data.emd = emd

        data.resize_to = resize_to
        
        return cls(data, **model_params, pretrained_path=str(model_file))
        
    @property
    def _model_metrics(self):
        return self.compute_metrics() 

    def _get_emd_params(self, save_inference_file):
        _emd_template = {}
        _emd_template["Framework"] = "arcgis.learn.models._inferencing"
        _emd_template["ModelConfiguration"] = "_cyclegan"
        _emd_template["InferenceFunction"] = "ArcGISImageTranslation.py"
        _emd_template["ModelType"] = "CycleGAN"
        _emd_template["n_channel"] = self._data.n_channel
        _emd_template["SupportsVariableTileSize"] = True
        if self._data._is_multispectral:
            _emd_template["NormalizationStats_b"] = {
                    "band_min_values": self._data._band_min_values_b,
                    "band_max_values": self._data._band_max_values_b,
                    "band_mean_values": self._data._band_mean_values_b,
                    "band_std_values": self._data._band_std_values_b,
                    "scaled_min_values": self._data._scaled_min_values_b,
                    "scaled_max_values": self._data._scaled_max_values_b,
                    "scaled_mean_values": self._data._scaled_mean_values_b,
                    "scaled_std_values": self._data._scaled_std_values_b
        }
            for _stat in _emd_template["NormalizationStats_b"]:
                    if _emd_template["NormalizationStats_b"][_stat] is not None:
                        _emd_template["NormalizationStats_b"][_stat] = _emd_template["NormalizationStats_b"][_stat].tolist()
        return _emd_template

    def show_results(self, rows=5):
        """
        Displays the results of a trained model on a part of the validation set.

        """
        self.learn.model.arcgis_results = True
        self.learn.show_results()
        if _IS_ARCGISPRONOTEBOOK:
            from matplotlib import pyplot as plt
            plt.show()
        self.learn.model.arcgis_results = False

    def predict(self, img_path, convert_to):
        """
        Predicts and display the image.

        =====================   ===========================================
        **Argument**            **Description**
        ---------------------   -------------------------------------------
        img_path                Required path of an image.
        ---------------------   -------------------------------------------
        convert_to              'A' if we want to generate image of type 'A' 
                                from type 'B' or 'B' if we want to generate 
                                image of type 'B' from type 'A' where A and
                                B are the domain specifications that were 
                                used while training.
        =====================   ===========================================

        """
        import numpy as np
        self.learn.model.arcgis_results = True
        img_path = Path(img_path)
        n_band = self._data.n_channel
        if self._data._is_multispectral:
            raw_img = ArcGISMSImage.open(img_path)
            if n_band > raw_img.shape[0]:
                cont = []
                last_tile = np.expand_dims(raw_img.data[raw_img.shape[0]-1,:,:], 0)
                res = abs(n_band - raw_img.shape[0])
                for i in range(res):
                    raw_img = Image(torch.tensor(np.concatenate((raw_img.data, last_tile), axis=0)))
        else:
            raw_img = open_image(img_path)
        raw_img_tuple = ImageTuple(raw_img, raw_img)
        pred_tuple = self.learn.predict(raw_img_tuple)
        if convert_to == 'A' or convert_to == 'a':
            pred_img = pred_tuple[1][0]/2+0.5
        elif convert_to == 'B' or convert_to == 'b':
            pred_img = pred_tuple[1][1]/2+0.5
        
        pred_img = transforms.ToPILImage()(pred_img).convert("RGB")
        self.learn.model.arcgis_results = False
        return pred_img

    def compute_metrics(self):
        """
        Computes Frechet Inception Distance (FID) on validation set.
        """
        fid_a = 'None'
        fid_b = 'None'
        
        if self._data._imagery_type_a == 'ms' and self._data._imagery_type_b == 'ms':
            logger.error("FID metric not supported for multispectral imagery type")
        else:
            if self._data._imagery_type_a == 'RGB' and self._data.n_channel == 3:
                fid_a = '{0:1.4e}'.format(compute_fid_metric(self, self._data, 'a'))
            if self._data._imagery_type_b == 'RGB' and self._data.n_channel == 3:
                fid_b = '{0:1.4e}'.format(compute_fid_metric(self, self._data, 'b'))
            
        return {'FID_A': fid_a,
                'FID_B': fid_b}

    @property
    def  supported_datasets(self):
        """ Supported dataset types for this model. """
        return CycleGAN._supported_datasets()

    @staticmethod
    def _supported_datasets():
        return ['CycleGAN'] 

        
