import numpy as np
import load_data as ld
import yaml
import argparse
import keras_utils
import os
import tensorflow as tf
from custom_accuracy import keras_accuracy, compute_image_probability_asloss, combine_predictions_each_batch, \
    compute_auc, list_localization_accuracy, compute_image_probability_production

os.environ["CUDA_VISIBLE_DEVICES"] = "1"


# gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.2)
# sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))

def load_config(path):
    with open(path, 'r') as ymlfile:
        return yaml.load(ymlfile)


parser = argparse.ArgumentParser()
parser.add_argument('-c', '--config_path', type=str,
                    help='Provide the file path to the configuration')

args = parser.parse_args()
config = load_config(args.config_path)

skip_processing = config['skip_processing_labels']
image_path = config['image_path']
classication_labels_path = config['classication_labels_path']
localization_labels_path = config['localization_labels_path']
results_path = config['results_path']
processed_labels_path = config['processed_labels_path']
train_mode = config['train_mode']
test_single_image = config['test_single_image']
prediction_skip_processing = config['prediction_skip_processing']
predict_res_path = config['prediction_results_path']


#######################################################################
def load_npy(file_name, res_path):
    return np.load(res_path + file_name, allow_pickle=True)


def get_index_label_prediction(file_set_name, res_path):
    prediction_file = 'predictions_' + file_set_name + '.npy'
    img_ind_file = 'image_indices_' + file_set_name + '.npy'
    patch_labels_file = 'patch_labels_' + file_set_name + '.npy'

    preds = load_npy(prediction_file, res_path)

    img_indices = load_npy(img_ind_file, res_path)
    patch_labs = load_npy(patch_labels_file, res_path)

    print(preds[1, :, :, 0])
    print(img_indices)
    return preds, img_indices, patch_labs


def get_label_prediction_image_level(preds, patch_labs, img_pred_as_loss):
    if img_pred_as_loss== 'as_loss':
        img_labels, img_prob_preds_v1 = compute_image_probability_asloss(preds, patch_labs, P=16)
    elif img_pred_as_loss== 'as_production':
        img_labels, img_prob_preds_v1 = compute_image_probability_production(preds, patch_labs, P=16)
    return img_labels, img_prob_preds_v1


def process_prediction_per_batch(predictions, patch_labels, img_pred_as_loss, l_bound, r_bound, coll_image_labs,
                                 coll_image_preds, coll_accurate_preds, coll_bbox_present, k):
    batch_pred = predictions[l_bound:r_bound, :, :, :]
    batch_patch_lab = patch_labels[l_bound:r_bound, :, :, :]

    #TODO: Different ways of predicting image label
    batch_img_labels, batch_img_preds_v1 = get_label_prediction_image_level(batch_pred, batch_patch_lab,
                                                                            img_pred_as_loss)


    _, acc_preds, nr_bbox_present = list_localization_accuracy(batch_patch_lab, batch_pred)

    with tf.Session().as_default() as sess:
        batch_image_labels, batch_image_predictions_v1 = batch_img_labels.eval(), batch_img_preds_v1.eval()

        coll_image_labs = combine_predictions_each_batch(batch_image_labels, coll_image_labs, k)
        coll_image_preds = combine_predictions_each_batch(batch_image_predictions_v1, coll_image_preds, k)
        print(batch_image_labels.shape)

    #with tf.Session() as sess:
        acc_predictions = np.asarray(sess.run(acc_preds))
        acc_predictions = np.expand_dims(acc_predictions, axis=0)

    #with tf.Session() as sess:
        total_bbox_present = np.asarray(sess.run(nr_bbox_present))
        total_bbox_present = np.expand_dims(total_bbox_present, axis=0)
    print("acc_loc, acc_preds, nr_bbox_present")
    print(k)

    coll_accurate_preds = combine_predictions_each_batch(acc_predictions, coll_accurate_preds, k)
    coll_bbox_present = combine_predictions_each_batch(total_bbox_present, coll_bbox_present, k)

    return coll_image_labs, coll_image_preds, coll_accurate_preds, coll_bbox_present


def process_prediction_all_batches(predictions, patch_labels, img_pred_as_loss, batch_size, file_unique_name, ind_file):
    # m = predictions.shape[0]  # number of training examples
    # batch_size = np.math.ceil(predictions.shape[0] / total_batch_nr)

    num_complete_minibatches = (np.floor(predictions.shape[0] / batch_size)).astype(int)

    coll_image_labels = 0
    coll_image_predictions = 0
    coll_accurate_preds = 0
    coll_bbox_present = 0

    for k in range(0, num_complete_minibatches):
        l_bound = k * batch_size
        r_bound = (k + 1) * batch_size
        coll_image_labels, coll_image_predictions, coll_accurate_preds, coll_bbox_present = process_prediction_per_batch(
            predictions,
            patch_labels, img_pred_as_loss, l_bound,
            r_bound, coll_image_labels,
            coll_image_predictions,
            coll_accurate_preds, coll_bbox_present,
            k)

    if predictions.shape[0] % batch_size != 0:
        l_bound = (num_complete_minibatches) * batch_size
        r_bound = predictions.shape[0]
        coll_image_labels, coll_image_predictions, coll_accurate_preds, coll_bbox_present = process_prediction_per_batch(
            predictions,
            patch_labels, img_pred_as_loss, l_bound,
            r_bound, coll_image_labels,
            coll_image_predictions,
            coll_accurate_preds,
            coll_bbox_present,
            k=1)

    np.save(results_path + '/image_labels_' + file_unique_name + '_'+img_pred_as_loss+ind_file, coll_image_labels)
    np.save(results_path + '/image_predictions_' + file_unique_name + '_'+img_pred_as_loss+ind_file, coll_image_predictions)
    np.save(results_path + '/accurate_localization_' + file_unique_name+ind_file, coll_accurate_preds)
    np.save(results_path + '/bbox_present_' + file_unique_name+ind_file, coll_bbox_present)
    return coll_image_labels, coll_image_predictions, coll_accurate_preds, coll_bbox_present


def process_prediction_v2(file_unique_name, res_path, img_pred_as_loss, batch_size):
    predictions, image_indices, patch_labels = get_index_label_prediction(file_unique_name, res_path)
    slice_size = 5000
    total_full_slices = predictions.shape[0]//slice_size
    print('Total full slices')
    print(predictions.shape)
    print(total_full_slices)
    for k in range(0, total_full_slices):
        start_ind = k*slice_size
        end_ind = start_ind+slice_size
        predictions_slice = predictions[start_ind:end_ind, :, :, :]
        patch_labels_slice = patch_labels[start_ind:end_ind, :, :, :]
        print("start end indices")
        print(start_ind)
        print(end_ind)
        print("Shape slices")
        print(predictions_slice.shape)
        print(patch_labels_slice.shape)

        coll_image_labels, coll_image_predictions, coll_accurate_preds, coll_bbox = process_prediction_all_batches(
            predictions_slice,
            patch_labels_slice, img_pred_as_loss, batch_size,
            file_unique_name, str(k))

        accurate_pred_all_batches = np.sum(coll_accurate_preds, axis=0)
        total_bbox_all_batches = np.sum(coll_bbox, axis=0)
        print("accurate pred all batches")
        print(accurate_pred_all_batches)
        print(total_bbox_all_batches)

    if predictions.shape[0] % slice_size != 0:
        start_ind = total_full_slices * slice_size

        predictions_slice = predictions[start_ind:predictions.shape[0], :, :, :]
        patch_labels_slice = patch_labels[start_ind:patch_labels.shape[0], :, :, :]
        print("Shape slices")
        print(predictions.shape)
        print(patch_labels.shape)

        coll_image_labels, coll_image_predictions, coll_accurate_preds, coll_bbox = process_prediction_all_batches(
            predictions_slice,
            patch_labels_slice, img_pred_as_loss, batch_size,
            file_unique_name, str(total_full_slices))

        accurate_pred_all_batches = np.sum(coll_accurate_preds, axis=0)
        total_bbox_all_batches = np.sum(coll_bbox, axis=0)
        print("accurate pred all batches")
        print(accurate_pred_all_batches)
        print(total_bbox_all_batches)


def process_prediction(file_unique_name, res_path, img_pred_as_loss, batch_size):

    predictions, image_indices, patch_labels = get_index_label_prediction(file_unique_name, res_path)
    # start_ind = 20000
    # end_ind = start_ind+10000
    # #CHANGE THIS
    # predictions = predictions[start_ind:end_ind, :, :, :]
    # patch_labels = patch_labels[start_ind:end_ind, :, :, :]
    print("sgape loaded models")
    print(predictions.shape)
    print(patch_labels.shape)

    coll_image_labels, coll_image_predictions, coll_accurate_preds, coll_bbox = process_prediction_all_batches(
        predictions,
        patch_labels, img_pred_as_loss, batch_size,
        file_unique_name)

    accurate_pred_all_batches = np.sum(coll_accurate_preds, axis=0)

    total_bbox_all_batches = np.sum(coll_bbox, axis=0)
    print("accurate pred all batches")
    print(accurate_pred_all_batches)
    print(total_bbox_all_batches)


def load_img_pred_labels(file_set, img_pred_as_loss, res_path):
    img_labs_file = 'image_labels_' + file_set + '_'+img_pred_as_loss+'.npy'
    img_preds_file = 'image_predictions_' + file_set + '_'+img_pred_as_loss+'.npy'

    img_preds = load_npy(img_labs_file, res_path)
    img_labels = load_npy(img_preds_file, res_path)
    img_labels = img_labels.astype(int)
    return img_labels, img_preds


def load_img_pred_labels_v2(file_set, img_pred_as_loss, file_ind, res_path):
    img_labs_file = 'image_labels_' + file_set + '_'+img_pred_as_loss+file_ind+'.npy'
    img_preds_file = 'image_predictions_' + file_set + '_'+img_pred_as_loss+ file_ind+'.npy'

    img_preds = load_npy(img_labs_file, res_path)
    img_labels = load_npy(img_preds_file, res_path)
    img_labels = img_labels.astype(int)
    return img_labels, img_preds


def load_accuracy_localization_v2(file_set, file_ind, res_path):
    acc_local = 'accurate_localization_' + file_set + file_ind+'.npy'
    bbox_present = 'bbox_present_' + file_set + file_ind+ '.npy'

    acc_local = load_npy(acc_local, res_path)
    bbox_present = load_npy(bbox_present, res_path)
    return acc_local, bbox_present


def load_accuracy_localization(file_set, res_path):
    acc_local = 'accurate_localization_' + file_set + '.npy'
    bbox_present = 'bbox_present_' + file_set + '.npy'

    acc_local = load_npy(acc_local, res_path)
    bbox_present = load_npy(bbox_present, res_path)
    return acc_local, bbox_present


def compute_save_accuracy(acc_loc, bbox_pres, file_unq_name):
    sum_acc_loc = np.sum(acc_loc, axis=0)
    sum_bbox_pres = np.sum(bbox_pres, axis=0)
    sum_acc_local_all = np.sum(acc_loc)
    sum_bbox_present_all = np.sum(bbox_pres)
    with np.errstate(divide='ignore', invalid='ignore'):
        acc_class = sum_acc_loc / sum_bbox_pres
        acc_avg = sum_acc_local_all/sum_bbox_present_all
    local_col_names = [ld.FINDINGS[i] for i in [0, 1, 4, 8, 9, 10, 12, 13]]
    keras_utils.save_evaluation_results(local_col_names, acc_class, 'accuracy_' + file_unq_name + '.csv',
                                        results_path, add_col='Avg_accuracy', add_value=acc_avg)


def do_predictions_set(data_set_name, skip_pred_process, img_pred_as_loss):
    if not skip_pred_process:
        process_prediction(data_set_name, results_path, img_pred_as_loss, batch_size=2)

    img_labels, img_preds = load_img_pred_labels(data_set_name, img_pred_as_loss, results_path)
    acc_local, bbox_present = load_accuracy_localization(data_set_name, results_path)

    compute_save_accuracy(acc_local, bbox_present, data_set_name)
    auc_all_classes_v1 = compute_auc(img_labels, img_preds)
    keras_utils.save_evaluation_results(ld.FINDINGS, auc_all_classes_v1, 'auc_prob_' + data_set_name + '_v1.csv',
                                        results_path)


#########################################################

# dataset_name = 'test_set'
# image_prediction_method = 'as_loss'
# do_predictions_set(dataset_name, prediction_skip_processing, image_prediction_method)
#
#
# dataset_name = 'train_set'
# image_prediction_method = 'as_loss'
# do_predictions_set(dataset_name, prediction_skip_processing, image_prediction_method)
#
# dataset_name = 'val_set'
# image_prediction_method = 'as_loss'
# do_predictions_set(dataset_name, prediction_skip_processing, image_prediction_method)


#####################################################################

def combine_npy_accuracy(data_set_name, res_path):
    coll_accuracy = 0
    coll_bbox_pres = 0
    for ind in range(0,8):
        print(ind)
        acc_local, bbox_present = load_accuracy_localization_v2(data_set_name, str(ind), res_path)
        print(coll_accuracy.shape)
        print(acc_local.shape)
        coll_accuracy = combine_predictions_each_batch(acc_local, coll_accuracy, ind)
        coll_bbox_pres = combine_predictions_each_batch(bbox_present, coll_bbox_pres, ind)

    compute_save_accuracy(coll_accuracy, coll_bbox_pres, data_set_name, res_path)


def combine_npy_auc(data_set_name, image_pred_method, res_path):
    coll_image_labels = 0
    coll_image_preds = 0

    for ind in range(1,10):
        img_labels, img_preds = load_img_pred_labels_v2(data_set_name, image_pred_method, str(ind), res_path)
        coll_image_preds = combine_predictions_each_batch(img_preds, coll_image_preds, ind-1)
        coll_image_labels = combine_predictions_each_batch(img_labels, coll_image_labels, ind - 1)

    auc_all_classes_v1 = compute_auc(coll_image_labels, coll_image_preds)
    keras_utils.save_evaluation_results(ld.FINDINGS, auc_all_classes_v1, 'auc_prob_' + data_set_name + '_v1.csv',
                                        res_path)



