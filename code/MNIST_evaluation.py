'''
Created on 15.12.2014

@author: Peter U. Diehl
'''

import brian as b
from brian import *
import numpy as np
import matplotlib
import matplotlib.cm as cmap
import time
import os.path
import scipy 
import cPickle as pickle
from struct import unpack
import brian.experimental.realtime_monitor as rltmMon


#------------------------------------------------------------------------------ 
# functions
#------------------------------------------------------------------------------     
def get_labeled_data(picklename, bTrain = True):
    """Read input-vector (image) and target class (label, 0-9) and return
       it as list of tuples.
    """
    if os.path.isfile('%s.pickle' % picklename):
        data = pickle.load(open('%s.pickle' % picklename))
    else:
        # Open the images with gzip in read binary mode
        if bTrain:
            images = open(MNIST_data_path + 'train-images.idx3-ubyte','rb')
            labels = open(MNIST_data_path + 'train-labels.idx1-ubyte','rb')
        else:
            images = open(MNIST_data_path + 't10k-images.idx3-ubyte','rb')
            labels = open(MNIST_data_path + 't10k-labels.idx1-ubyte','rb')
        # Get metadata for images
        images.read(4)  # skip the magic_number
        number_of_images = unpack('>I', images.read(4))[0]
        rows = unpack('>I', images.read(4))[0]
        cols = unpack('>I', images.read(4))[0]
        # Get metadata for labels
        labels.read(4)  # skip the magic_number
        N = unpack('>I', labels.read(4))[0]
        if number_of_images != N:
            raise Exception('number of labels did not match the number of images')
        # Get the data
        x = np.zeros((N, rows, cols), dtype=np.uint8)  # Initialize numpy array
        y = np.zeros((N, 1), dtype=np.uint8)  # Initialize numpy array
        for i in xrange(N):
            if i % 1000 == 0:
                print("i: %i" % i)
            x[i] = [[unpack('>B', images.read(1))[0] for unused_col in xrange(cols)]  for unused_row in xrange(rows) ]
            y[i] = unpack('>B', labels.read(1))[0]
        data = {'x': x, 'y': y, 'rows': rows, 'cols': cols}
        pickle.dump(data, open("%s.pickle" % picklename, "wb"))
    return data

def get_recognized_number_ranking(assignments, spike_rates):
    summed_rates = [0] * 10
    num_assignments = [0] * 10
    for i in xrange(10):
        num_assignments[i] = len(np.where(assignments == i)[0])
        if num_assignments[i] > 0:
            summed_rates[i] = np.sum(spike_rates[assignments == i]) / num_assignments[i]
    return np.argsort(summed_rates)[::-1]

def get_new_assignments(result_monitor, input_numbers):
    print result_monitor.shape
    assignments = np.ones(n_e) * -1 # initialize them as not assigned
    input_nums = np.asarray(input_numbers)
    maximum_rate = [0] * n_e    
    for j in xrange(10):
        num_inputs = len(np.where(input_nums == j)[0])
        if num_inputs > 0:
            rate = np.sum(result_monitor[input_nums == j], axis = 0) / num_inputs
        for i in xrange(n_e):
            if rate[i] > maximum_rate[i]:
                maximum_rate[i] = rate[i]
                assignments[i] = j 
    return assignments


MNIST_data_path = '../data/'
data_path = '../activity/eth_model_activity/'

training_ending = raw_input('Enter number of training samples: ')
if training_ending == '':
    training_ending = '10000'

testing_ending = raw_input('Enter number of test examples: ')
if testing_ending == '':
    testing_ending = '10000'

start_time_training = 0
end_time_training = int(training_ending)
start_time_testing = 0
end_time_testing = int(testing_ending)


# input and square root of input
n_input = 784
n_input_sqrt = int(math.sqrt(n_input))

# size of convolution windows
n_e = raw_input('Enter number of excitatory neurons: ')
if n_e == '':
    n_e = 100
else:
    n_e = int(n_e)

n_e_sqrt = int(math.sqrt(n_e))

# number of inhibitory neurons (number of convolutational features (for now))
n_i = n_e

# determine STDP rule to use
stdp_input = ''

if raw_input('Use weight dependence (default no)?: ') in [ 'no', '' ]:
	use_weight_dependence = False
	stdp_input += 'no_weight_dependence_'
else:
	use_weight_dependence = True
	stdp_input += 'weight_dependence_'

if raw_input('Enter (yes / no) for post-pre (default yes): ') in [ 'yes', '' ]:
	post_pre = True
	stdp_input += 'postpre'
else:
	post_pre = False
	stdp_input += 'no_postpre'

# set ending of filename saves
ending = '_' + stdp_input + str(n_e)

n_input = 784
ending = ''


print '...loading MNIST'
training = get_labeled_data(MNIST_data_path + 'training')
testing = get_labeled_data(MNIST_data_path + 'testing', bTrain = False)

print '...loading results'
training_result_monitor = np.load(data_path + 'resultPopVecs' + training_ending + '_' + stdp_input + '.npy')
training_input_numbers = np.load(data_path + 'inputNumbers' + training_ending + '_' + stdp_input + '.npy')
testing_result_monitor = np.load(data_path + 'resultPopVecs' + testing_ending + '_' + stdp_input + '.npy')
testing_input_numbers = np.load(data_path + 'inputNumbers' + testing_ending + '_' + stdp_input + '.npy')

print '...getting assignments'
test_results = np.zeros((10, end_time_testing - start_time_testing))
test_results_max = np.zeros((10, end_time_testing - start_time_testing))
test_results_top = np.zeros((10, end_time_testing - start_time_testing))
test_results_fixed = np.zeros((10, end_time_testing - start_time_testing))
assignments = get_new_assignments(training_result_monitor[start_time_training : end_time_training], training_input_numbers[start_time_training : end_time_training])


counter = 0 
num_tests = end_time_testing / 10000
sum_accurracy = [0] * num_tests
while (counter < num_tests):
    end_time = min(end_time_testing, 10000*(counter+1))
    start_time = 10000*counter
    test_results = np.zeros((10, end_time-start_time))
    print 'calculate accuracy for sum'
    for i in xrange(end_time - start_time):
        test_results[:,i] = get_recognized_number_ranking(assignments, testing_result_monitor[i+start_time,:])
    difference = test_results[0,:] - testing_input_numbers[start_time:end_time]
    correct = len(np.where(difference == 0)[0])
    incorrect = np.where(difference != 0)[0]
    sum_accurracy[counter] = correct/float(end_time-start_time) * 100
    print 'Sum response - accuracy: ', sum_accurracy[counter], ' num incorrect: ', len(incorrect)
    counter += 1
print 'Sum response - accuracy --> mean: ', np.mean(sum_accurracy),  '--> standard deviation: ', np.std(sum_accurracy)


b.show()
