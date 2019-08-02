import os
os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"]=""  # specify which GPU(s) to be used

import math
import numpy as np
import tensorflow as tf

import keras
import keras.backend as K
from keras.layers import Dense, Conv2D, Input, Flatten, MaxPool2D
from keras.models import Model
from keras.optimizers import Adam
from keras.datasets import mnist
from keras.preprocessing.image import ImageDataGenerator
from keras.callbacks import ModelCheckpoint, LearningRateScheduler
import pickle
#from keras.utils import multi_gpu_model
from keras.layers import Lambda
from keras.layers import average

def reduce_mean(softmax):
    return tf.reduce_mean(softmax, 0)

def crop(x_input, loc_x, loc_y ):
    return x_input[:, loc_x - 10:loc_x + 10, loc_y - 10:loc_y + 10, :]
    #return x_input

loc = [[10, 10]]
#loc = [[10, 10], [10, 14]]#, [10, 18], [14, 10], [14, 14], [14, 18], [18, 10], [18, 14], [18, 18]]

def create_Model_single(x_input):
    layer1_out = x = Conv2D(filters=32, kernel_size=5)(x_input)
    x = MaxPool2D(pool_size=2)(x)
    x = Conv2D(filters=64, kernel_size=5)(x)
    x = MaxPool2D(pool_size=2)(x)
    x = Flatten()(x)
    x = Dense(1024)(x)
    softmax_i = Dense(10,activation='softmax')(x)
    return softmax_i, layer1_out

def create_Model(x_input_shape):
    layer1_out_list = []
    softmax_list = []
    inputs = Input(shape = x_input_shape)
    for i, loc_i in enumerate(loc):
        loc_x, loc_y = loc_i
        pre_process = Lambda(crop, arguments={'loc_x':loc_x, 'loc_y':loc_y })(inputs)
        softmax_i, layer1_out = create_Model_single(pre_process)
        softmax_list += [softmax_i]
        layer1_out_list += [layer1_out]
    softmax = keras.layers.average(softmax_list*2)
    model = Model(inputs=inputs, outputs=softmax)
    return model, layer1_out_list

def yopo_adversary_generator(datagen, x, logits, m, n):
    old_generator = datagen.flow(x, logits, batch_size=args_batch_size)
    for x_batch, logits_batch in old_generator:
        eta = np.random.uniform(-args_eps, args_eps, x_batch.shape)
        x_new_batch = x_batch + eta
        for i in range(m + 1):
            yield x_new_batch, logits_batch
            if i == m:
                break
            # Add perturbation to inputs. loss_layer1 is only computed once.
            loss_layer1 = sess.run(loss_layer1_t, feed_dict={input_xs: x_new_batch, targets_ys: logits_batch, sample_weights_ys: [1] * len(x_batch)})
            for j in range(n):
                loss_layer1_value = np.stack(loss_layer1, 0).transpose(1, 0, 2, 3, 4)
                grad = sess.run(yopo_grad_t, feed_dict={input_xs: x_new_batch, p_layer1_t: loss_layer1_value})
                #grad = sess.run(yopo_grad_t, feed_dict={input_xs: x_new_batch, p_layer1_t: loss_layer1})
                grad = np.sign(grad)
                x_new_batch += args_step_size * grad
                x_new_batch = np.clip(x_new_batch, x_batch - args_eps, x_batch + args_eps)
                x_new_batch = np.clip(x_new_batch, 0.0, 1.0)


# This is the (inputs, targets) generator for training
def adversary_generator(datagen, x, logits):
  old_generator = datagen.flow(x, logits, batch_size=args_batch_size)
  for x_batch, logits_batch in old_generator:
    x_new_batch = np.copy(x_batch)
    for i in range(args_step_num):
        grad = sess.run(grad_t, feed_dict={input_xs: x_new_batch, targets_ys: logits_batch, sample_weights_ys: [1] * len(x_batch)})
        grad = np.sign(grad)
        x_new_batch += args_step_size * grad
        x_new_batch = np.clip(x_new_batch, x_batch - args_eps, x_batch + args_eps)
        x_new_batch = np.clip(x_new_batch, 0.0, 1.0)
    yield x_new_batch, logits_batch

def lr_schedule(epoch):
  lr = 0.1
  if epoch > int(100 / args_lr_m):
    lr = 0.01
  elif epoch > int(150 / args_lr_m):
    lr = 0.001
  return lr

def sparse_loss_with_logits(y_train, pre_softmax_i):
    return tf.nn.sparse_softmax_cross_entropy_with_logits(labels=y_train, logits=pre_softmax_i)

if __name__ == '__main__':

    if 0:
        FLAGS = tf.app.flags.FLAGS
        tfconfig = tf.ConfigProto(
            allow_soft_placement=True,
            log_device_placement=True
        )
        tfconfig.gpu_options.allow_growth = True
        sess = tf.Session(config=tfconfig)
    else:
        sess= tf.Session()
    K.set_session(sess)
    # sess.run(tf.global_variables_initializer())
    # sess.run(tf.local_variables_initializer())

    # Set parameters here
    args_step_size = 2.0 / 255.0
    args_eps = 8.0 / 255.0
    args_step_num = 7
    args_batch_size = 32
    args_yopo_m = 3  # m in YOPO-m-n
    args_yopo_n = 5  # n in YOPO-m-n
    args_lr_m = 3  # 1 if standard adversarial training, or use free_m or yopo_m
    num_classes = 10
    np.random.seed(2019)
    tf.set_random_seed(9102)

    (x_train, y_train), (x_test, y_test) = mnist.load_data()
    x_train = x_train.astype('float32') / 255.0
    x_train = np.expand_dims(x_train, -1)

    x_test = x_test.astype('float32') / 255.0
    x_test = np.expand_dims(x_test, -1)
    logits_train, logits_test = keras.utils.to_categorical(y_train, num_classes), keras.utils.to_categorical(y_test, num_classes)

    train_datagen = ImageDataGenerator()
    train_datagen.fit(x_train)
    test_datagen = ImageDataGenerator()

    lr_scheduler = LearningRateScheduler(lr_schedule)
    filepath = "saved-model-{epoch:02d}-{val_acc:.2f}.hdf5"
    checkpoint = ModelCheckpoint(filepath, monitor='val_acc', verbose=1, save_best_only=True, mode='max')
    callbacks = [lr_scheduler]#, checkpoint]

    epochs = math.ceil(300 / args_yopo_m)
    opt = Adam(lr=1e-4, beta_1=0.5)

    model, layer1_out_list = create_Model(x_train[0].shape)

    #model = multi_gpu_model(model, gpus=4)
    model.compile(loss ='categorical_crossentropy', optimizer=opt,metrics=['categorical_accuracy'])

    # Standard adversarial
    input_xs = model.input
    output_ys = model.output
    targets_ys = model.targets[0]
    sample_weights_ys = model.sample_weights[0]
    loss_t = model.total_loss
    grad_t = K.gradients(loss_t, input_xs)[0]

    # YOPO
    yopo_grad_t = []
    for i, layer1_out in enumerate(layer1_out_list):
        loss_layer1_t = K.gradients(loss_t, layer1_out)  # gradient of loss w.r.t. the output of the first layer
        p_layer1_t = K.placeholder([len(loc)]+layer1_out.get_shape().as_list(), dtype=tf.float32) #[len(loc)]
        p_layer1_t = tf.transpose(p_layer1_t, (1, 0, 2, 3, 4))
        hamilton_layer1_t = keras.layers.dot([Flatten()(p_layer1_t), Flatten()(layer1_out)], axes=1)
        yopo_grad_t += [K.gradients(hamilton_layer1_t, input_xs)[0]]  # YOPO approximation of grad_t

    yopo_grad_t = keras.layers.average(yopo_grad_t*2,)


    sess.run(tf.global_variables_initializer())
    if 1:
        # FOR DEBUG
        x_new_batch, logits_batch = train_datagen.flow(x_train, logits_train).next()
        loss_layer1 = sess.run(loss_layer1_t, feed_dict={input_xs: x_new_batch, targets_ys: logits_batch,
                                       sample_weights_ys: [1] * len(x_new_batch)})
        loss_layer1_value = np.stack(loss_layer1, 0).transpose(1,0,2,3,4)
        grad = sess.run(yopo_grad_t, feed_dict={input_xs: x_new_batch, p_layer1_t: loss_layer1_value})
        exit()
    if 0:
        # FOR DEBUG
        x_new_batch, logits_batch = train_datagen.flow(x_test, logits_test).next()
        grad = sess.run(grad_t, feed_dict={input_xs: x_new_batch, targets_ys: logits_batch, sample_weights_ys: [1] * len(x_new_batch)})
        exit()


    history = model.fit_generator(yopo_adversary_generator(train_datagen, x_train, logits_train, args_yopo_m, args_yopo_n),
                        validation_data=adversary_generator(test_datagen, x_test, logits_test),
                        validation_steps=math.ceil(len(x_test) / args_batch_size),
                        epochs=epochs, verbose=1, workers=4, use_multiprocessing= True,
                        callbacks=callbacks,
                        steps_per_epoch=math.ceil(len(x_train) / args_batch_size) * (args_yopo_m + 1))

    """with open('/trainHistoryDict', 'wb') as file_pi:
        pickle.dump(history.history, file_pi)"""