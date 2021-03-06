'''
Much the same as 'spiking_MNIST.py', but we instead use a number of convolutional
windows to map the input to a reduced space.
'''

import numpy as np
import matplotlib.cm as cmap
import time, os.path, scipy, math, sys, timeit
import cPickle as p
import brian_no_units
import brian as b

from scipy.sparse import coo_matrix
from struct import unpack
from brian import *

np.set_printoptions(threshold=np.nan)

# only show log messages of level ERROR or higher
b.log_level_error()

# specify the location of the MNIST data
MNIST_data_path = '../data/'


def get_labeled_data(picklename, bTrain = True):
    '''
    Read input-vector (image) and target class (label, 0-9) and return it as 
    a list of tuples.
    '''
    if os.path.isfile('%s.pickle' % picklename):
        data = p.load(open('%s.pickle' % picklename))
    else:
        # Open the images with gzip in read binary mode
        if bTrain:
            images = open(MNIST_data_path + 'train-images-idx3-ubyte', 'rb')
            labels = open(MNIST_data_path + 'train-labels-idx1-ubyte', 'rb')
        else:
            images = open(MNIST_data_path + 't10k-images-idx3-ubyte', 'rb')
            labels = open(MNIST_data_path + 't10k-labels-idx1-ubyte', 'rb')

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
                print('...loading progress: %i-th datum' % i)
            x[i] = [[unpack('>B', images.read(1))[0] for unused_col in xrange(cols)]  for unused_row in xrange(rows) ]
            y[i] = unpack('>B', labels.read(1))[0]

        data = {'x': x, 'y': y, 'rows': rows, 'cols': cols}
        p.dump(data, open('%s.pickle' % picklename, 'wb'))
    
    return data


def get_matrix_from_file(file_name, n_src, n_tgt):
    '''
    Given the name of a file pointing to a .npy ndarray object, load it into
    'weight_matrix' and return it
    '''

    # load the stored ndarray into 'readout', instantiate 'weight_matrix' as 
    # correctly-shaped zeros matrix
    readout = np.load(file_name)
    weight_matrix = np.zeros((n_src, n_tgt))

    # read the 'readout' ndarray values into weight_matrix by (row, column) indices
    weight_matrix[np.int32(readout[:,0]), np.int32(readout[:,1])] = readout[:,2]

    # return the weight matrix read from file
    return weight_matrix


def save_connections():
    '''
    Save all connections in 'save_conns'; ending may be set to the index of the last
    example run through the network
    '''
    # print out saved connections
    print '...saving connections: weights/conv_full_conn_weights/' + save_conns[0] + '_' + stdp_input

    # iterate over all connections to save
    for conn_name in save_conns:
        # get the connection matrix for this connection
        conn_matrix = input_connections[conn_name][:]
        # sparsify it into (row, column, entry) tuples
        conn_list_sparse = ([(i, j, conn_matrix[i, j]) for i in xrange(conn_matrix.shape[0]) for j in xrange(conn_matrix.shape[1]) ])
        # save it out to disk
        np.save(data_path + 'weights/conv_full_conn_weights/' + conn_name + '_' + stdp_input, conn_list_sparse)


def save_theta():
    '''
    Save the adaptive threshold parameters to a file.
    '''
    # iterate over population for which to save theta parameters
    for pop_name in population_names:
    	# print out saved theta populations
        print '...saving theta: weights/conv_full_conn_weights/theta_' + pop_name + '_' + ending + '_' + stdp_input

        # save out the theta parameters to file
        np.save(data_path + 'weights/conv_full_conn_weights/theta_' + pop_name + '_' + ending + '_' + stdp_input, neuron_groups[pop_name + 'e'].theta)


def set_weights_most_fired():
    '''
    For each convolutional patch, set the weights to those of the neuron which
    fired the most in the last iteration.
    '''
    for conn_name in input_connections:
        if conn_name == 'X_CONV1':
            for feature in xrange(conv_features):
                # count up the spikes for the neurons in this convolution patch
                column_sums = np.sum(current_conv_spike_count[feature : feature + 1, :], axis=0)

                # find the excitatory neuron which spiked the most
                most_spiked = np.argmax(column_sums)

                # create a "dense" version of the most spiked excitatory neuron's weight
                most_spiked_dense = input_connections[conn_name][:, feature * n_e_patch + most_spiked].todense()

                # set all other neurons' (in the same convolution patch) weights the same as the most-spiked neuron in the patch
                for n in xrange(n_e_patch):
                    if n != most_spiked:
                        other_dense = input_connections[conn_name][:, feature * n_e_patch + n].todense()
                        other_dense[convolution_locations[n]] = most_spiked_dense[convolution_locations[most_spiked]]
                        input_connections[conn_name][:, feature * n_e_patch + n] = other_dense


def normalize_weights():
    '''
    Squash the input -> excitatory weights to sum to a prespecified number.
    '''
    for conn_name in input_connections:
        if conn_name == 'X_CONV1':
            connection = input_connections[conn_name][:].todense()
            for feature in xrange(conv_features):
                feature_connection = connection[:, feature * n_e_patch : (feature + 1) * n_e_patch]
                column_sums = np.sum(feature_connection, axis=0)
                column_factors = weight['XeCONV1e'] / column_sums

                for n in xrange(n_e_patch):
                    dense_weights = input_connections[conn_name][:, feature * n_e_patch + n].todense()
                    dense_weights[convolution_locations[n]] *= column_factors[n]
                    input_connections[conn_name][:, feature * n_e_patch + n] = dense_weights

        elif conn_name == 'CONV1_FULL1':
            connection = input_connections[conn_name][:]
            temp_conn = np.copy(connection)
            column_sums = np.sum(temp_conn, axis=0)
            column_factors = weight['CONV1eFULL1e'] / column_sums
            for idx in xrange(n_e_full):
                connection[:, idx] *= column_factors[idx]


def plot_input():
    '''
    Plot the current input example during the training procedure.
    '''
    fig = b.figure(fig_num, figsize = (6, 6))
    im3 = b.imshow(rates.reshape((28, 28)), interpolation = 'nearest', vmin=0, vmax=64, cmap=cmap.get_cmap('gray'))
    b.colorbar(im3)
    b.title('Current input example')
    fig.canvas.draw()
    return im3, fig


def update_input(im3, fig):
    '''
    Update the input image to use for input plotting.
    '''
    im3.set_array(rates.reshape((28, 28)))
    fig.canvas.draw()
    return im3


def get_2d_input_weights():
    '''
    Get the weights from the input to excitatory layer and reshape it to be two-dimensional and user-viewable.
    '''
    rearranged_weights = np.zeros(( conv_features * conv_size, conv_size * n_e_patch ))
    
    # counts number of input -> excitatory weights displayed so far
    connection = input_connections['X_CONV1'][:]

    # for each convolution feature
    for feature in xrange(conv_features):
        # for each excitatory neuron in this convolution feature
        for n in xrange(n_e_patch):
            # get the connection weights from the input to this neuron
            temp = connection[:, feature * n_e_patch + n].todense()
            # add it to the rearranged weights for displaying to the user
            rearranged_weights[feature * conv_size : (feature + 1) * conv_size, n * conv_size : (n + 1) * conv_size] = temp[convolution_locations[n]].reshape((conv_size, conv_size))

    # return the rearranged weights to display to the user
    return rearranged_weights.T


def plot_2d_input_weights():
    '''
    Plot the weights from input to excitatory layer to view during training.
    '''
    weights = get_2d_input_weights()
    fig = b.figure(fig_num, figsize=(18, 18))
    im2 = b.imshow(weights, interpolation='nearest', vmin=0, vmax=wmax_ee, cmap=cmap.get_cmap('hot_r'))
    b.colorbar(im2)
    b.title('2D weights (input -> convolutional)')
    fig.canvas.draw()
    return im2, fig


def update_2d_input_weights(im, fig):
    '''
    Update the plot of the weights from input to excitatory layer to view during training.
    '''
    weights = get_2d_input_weights()
    im.set_array(weights)
    fig.canvas.draw()
    return im


def get_2d_conv_weights():
    '''
    Get the weights from the convolutional layer to fully-connected layer and reshape it to be user-viewable.
    '''
    # counts number of input -> excitatory weights displayed so far
    weight_matrix = np.copy(input_connections['CONV1_FULL1'][:]).T

    # intermediate values for dimensionality of 2D weights
    n_e_full_sqrt = int(np.sqrt(n_e_full))

    # dimensionality of 2D weights 
    num_values_col = n_e_full_sqrt * conv_features
    num_values_row = n_e_full_sqrt * n_e_patch
    rearranged_weights = np.zeros((num_values_col, num_values_row))

    # setting 2D weights
    for i in xrange(conv_features):
        for j in xrange(n_e_patch):
            rearranged_weights[i * n_e_full_sqrt : (i + 1) * n_e_full_sqrt, j * n_e_full_sqrt : (j + 1) * n_e_full_sqrt] = weight_matrix[:, i + j * n_e_full_sqrt].reshape((n_e_full_sqrt, n_e_full_sqrt))

    # return the rearranged weights
    return rearranged_weights


def plot_2d_conv_weights():
    '''
    Plot the weights from input to excitatory layer to view during training.
    '''
    weights = get_2d_conv_weights()
    fig = b.figure(fig_num, figsize=(18, 18))
    im2 = b.imshow(weights, interpolation='nearest', vmin=0, vmax=wmax_ee, cmap=cmap.get_cmap('hot_r'))
    b.colorbar(im2)
    b.title('2D weights (input -> convolutional, convolutional -> fully-connected)')
    fig.canvas.draw()
    return im2, fig


def update_2d_conv_weights(im, fig):
    '''
    Update the plot of the weights from input to excitatory layer to view during training.
    '''
    weights = get_2d_conv_weights()
    im.set_array(weights)
    fig.canvas.draw()
    return im


def get_current_performance(performance, current_example_num):
    '''
    Evaluate the performance of the network on the past 'update_interval' training
    examples.
    '''
    current_evaluation = int(current_example_num / update_interval)
    start_num = current_example_num - update_interval
    end_num = current_example_num
    difference = output_numbers[start_num:end_num, 0] - input_numbers[start_num:end_num]
    correct = len(np.where(difference == 0)[0])
    performance[current_evaluation] = correct / float(update_interval) * 100
    return performance


def plot_performance(fig_num):
    '''
    Set up the performance plot for the beginning of the simulation.
    '''
    num_evaluations = int(num_examples / update_interval)
    time_steps = range(0, num_evaluations)
    performance = np.zeros(num_evaluations)
    fig = b.figure(fig_num, figsize = (5, 5))
    fig_num += 1
    ax = fig.add_subplot(111)
    im2, = ax.plot(time_steps, performance) #my_cmap
    b.ylim(ymax = 100)
    b.title('Classification performance')
    fig.canvas.draw()
    return im2, performance, fig_num, fig


def update_performance_plot(im, performance, current_example_num, fig):
    '''
    Update the plot of the performance based on results thus far.
    '''
    performance = get_current_performance(performance, current_example_num)
    im.set_ydata(performance)
    fig.canvas.draw()
    return im, performance


def get_recognized_number_ranking(assignments, spike_rates):
    '''
    Given the label assignments of the excitatory layer and their spike rates over
    the past 'update_interval', get the ranking of each of the categories of input.
    '''
    summed_rates = [0] * 10
    num_assignments = [0] * 10
    for i in xrange(10):
        num_assignments[i] = len(np.where(assignments == i)[0])
        if num_assignments[i] > 0:
            summed_rates[i] = np.sum(spike_rates[assignments == i]) / num_assignments[i]
    return np.argsort(summed_rates)[::-1]


def get_new_assignments(result_monitor, input_numbers):
    '''
    Based on the results from the previous 'update_interval', assign labels to the
    excitatory neurons.
    '''
    assignments = np.zeros(n_e_full)
    input_nums = np.asarray(input_numbers)
    maximum_rate = np.zeros(n_e_full)
    
    for j in xrange(10):
        num_assignments = len(np.where(input_nums == j)[0])
        if num_assignments > 0:
            rate = np.sum(result_monitor[input_nums == j], axis = 0) / num_assignments
            for i in xrange(n_e_full):
                if rate[i] > maximum_rate[i]:
                    maximum_rate[i] = rate[i]
                    assignments[i] = j

    return assignments

##############
# LOAD MNIST #
##############

if raw_input('Enter "test" for testing mode, "train" for training mode (default training mode): ') == 'test':
    test_mode = True
else:
    test_mode = False

if not test_mode:
    start = time.time()
    training = get_labeled_data(MNIST_data_path + 'training')
    end = time.time()
    print 'time needed to load training set:', end - start

else:
    start = time.time()
    testing = get_labeled_data(MNIST_data_path + 'testing', bTrain = False)
    end = time.time()
    print 'time needed to load test set:', end - start

################################
# SET PARAMETERS AND EQUATIONS #
################################

b.set_global_preferences(
                        defaultclock = b.Clock(dt=0.5*b.ms), # The default clock to use if none is provided or defined in any enclosing scope.
                        useweave = True, # Defines whether or not functions should use inlined compiled C code where defined.
                        gcc_options = ['-ffast-math -march=native'],  # Defines the compiler switches passed to the gcc compiler. 
                        #For gcc versions 4.2+ we recommend using -march=native. By default, the -ffast-math optimizations are turned on 
                        usecodegen = True,  # Whether or not to use experimental code generation support.
                        usecodegenweave = True,  # Whether or not to use C with experimental code generation support.
                        usecodegenstateupdate = True,  # Whether or not to use experimental code generation support on state updaters.
                        usecodegenthreshold = False,  # Whether or not to use experimental code generation support on thresholds.
                        usenewpropagate = True,  # Whether or not to use experimental new C propagation functions.
                        usecstdp = True,  # Whether or not to use experimental new C STDP.
                        openmp = False, # whether or not to use OpenMP pragmas in generated C code.
                        magic_useframes = False, # defines whether or not the magic functions should serach for objects defined only in the calling frame,
                                                # or if they should find all objects defined in any frame. Set to "True" if not in an interactive shell.
                        useweave_linear_diffeq = True, # Whether to use weave C++ acceleration for the solution of linear differential equations.
                       )

# for reproducibility's sake
np.random.seed(0)

# where the MNIST data files are stored
data_path = '../'

# set parameters for simulation based on train / test mode
if test_mode:
    weight_path = data_path + 'weights/conv_full_conn_weights/'
    num_examples = 10000 * 1
    use_testing_set = True
    do_plot_performance = False
    record_spikes = True
    ee_STDP_on = False
else:
    weight_path = data_path + 'random/conv_full_conn_random/'
    num_examples = 60000 * 1
    use_testing_set = False
    do_plot_performance = True
    record_spikes = True
    ee_STDP_on = True

# plotting or not
do_plot = True

# number of inputs to the network
n_input = 784
n_input_sqrt = int(math.sqrt(n_input))

# size of convolution windows
conv_size = raw_input('Enter size of square side length of convolution window (default 27): ')
if conv_size == '':
    conv_size = 27
else:
    conv_size = int(conv_size)

# stride of convolution windows
conv_stride = raw_input('Enter stride size of convolution window (default 1): ')
if conv_stride == '':
    conv_stride = 1
else:
    conv_stride = int(conv_stride)

# number of convolution features
conv_features = raw_input('Enter number of convolution features to learn (default 10): ')
if conv_features == '':
    conv_features = 10
else:
    conv_features = int(conv_features)

# size of the fully-connected laye (used for "voting" / classification)
full_size = raw_input('Enter number of neurons in the fully-connected layer (default 25): ')
if full_size == '':
    full_size = 25
else:
    full_size = int(full_size)

# number of excitatory neurons (number output from convolutional layer)
n_e_patch = ((n_input_sqrt - conv_size) / conv_stride + 1) ** 2
n_e_conv = n_e_patch * conv_features
n_e_patch_sqrt = int(math.sqrt(n_e_patch))

# number of inhibitory neurons in convolutional layer (number of convolutational patches (for now))
n_i_conv = conv_features

# number of excitatory, inhibitory neurons in fully-connected layer
n_e_full = n_i_full = full_size

# total number of excitatory, inhibitory neurons
n_e_total = n_e_conv + n_e_full
n_i_total = n_i_conv + n_i_full

# file identifier (?)
ending = str(conv_size) + '_' + str(conv_stride) + '_' + str(conv_features) + '_' + str(n_e_patch) + '_' + str(n_e_full)

# time (in seconds) per data example presentation
single_example_time = 0.35 * b.second

# time (in seconds) per rest period between data examples
resting_time = 0.15 * b.second

# total runtime (number of examples times (presentation time plus rest period))
runtime = num_examples * (single_example_time + resting_time)

# set the update interval
if test_mode:
    update_interval = num_examples
else:
    update_interval = 100

# set weight update interval (plotting)
weight_update_interval = 1

# set progress printing interval
print_progress_interval = 10

# rest potential parameters, reset potential parameters, threshold potential parameters, and refractory periods
v_rest_e = -65. * b.mV
v_rest_i = -60. * b.mV
v_reset_e = -65. * b.mV
v_reset_i = -45. * b.mV
v_thresh_e = -52. * b.mV
v_thresh_i = -40. * b.mV
refrac_e = 5. * b.ms
refrac_i = 2. * b.ms

# dictionaries for weights and delays
weight = {}
delay = {}

# naming neuron populations (X for input, A for population, XA for input -> connection, etc...
input_population_names = [ 'X' ]
population_names = [ 'CONV1', 'FULL1' ]
input_connection_names = [ 'X_CONV1', 'CONV1_FULL1' ]
save_conns = [ 'XeCONV1e', 'CONV1eFULL1e' ]
input_connection_types = [ 'eeX_CONV1', 'eeCONV1_FULL1' ]
recurrent_connection_types = [ 'ei', 'ie' ]

# weight and delay parameters
weight['XeCONV1e'] = (conv_size ** 2) / 7.0
weight['CONV1eFULL1e'] = full_size / 7.0
delay['eeX_CONV1'] = (0 * b.ms, 10 * b.ms)
delay['CONV1eCONV1i'] = (0 * b.ms, 5 * b.ms)
delay['eeCONV1_FULL1'] = (0 * b.ms, 10 * b.ms)
delay['FULL1eFULL1i'] = (0 * b.ms, 5 * b.ms)

# intensity of input
input_intensity = start_input_intensity = 2.0

# time constants, learning rates, max weights, weight dependence, etc.
tc_pre_ee = 20 * b.ms
tc_post_ee = 20 * b.ms
nu_ee_pre =  0.0001
nu_ee_post = 0.01
wmax_ee = 1.0
exp_ee_post = exp_ee_pre = 0.2
w_mu_pre = 0.2
w_mu_post = 0.2

# setting up differential equations (depending on train / test mode)
if test_mode:
    scr_e = 'v = v_reset_e; timer = 0*ms'
else:
    tc_theta = 1e7 * b.ms
    theta_plus_e = 0.05 * b.mV
    scr_e = 'v = v_reset_e; theta += theta_plus_e; timer = 0*ms'

offset = 20.0 * b.mV
v_thresh_e = '(v>(theta - offset + ' + str(v_thresh_e) + ')) * (timer>refrac_e)'

# equations for neurons
neuron_eqs_e = '''
        dv/dt = ((v_rest_e - v) + (I_synE + I_synI) / nS) / (100 * ms)  : volt
        I_synE = ge * nS *         -v                           : amp
        I_synI = gi * nS * (-100.*mV-v)                          : amp
        dge/dt = -ge/(1.0*ms)                                   : 1
        dgi/dt = -gi/(2.0*ms)                                  : 1
        '''
if test_mode:
    neuron_eqs_e += '\n  theta      :volt'
else:
    neuron_eqs_e += '\n  dtheta/dt = -theta / (tc_theta)  : volt'

neuron_eqs_e += '\n  dtimer/dt = 100.0 : ms'

neuron_eqs_i = '''
        dv/dt = ((v_rest_i - v) + (I_synE + I_synI) / nS) / (10*ms)  : volt
        I_synE = ge * nS *         -v                           : amp
        I_synI = gi * nS * (-85.*mV-v)                          : amp
        dge/dt = -ge/(1.0*ms)                                   : 1
        dgi/dt = -gi/(2.0*ms)                                  : 1
        '''

# equations for neurons
neuron_eqs_e_fc = '''
        dv/dt = ((v_rest_e - v) + (I_synE + I_synI) / nS) / (100 * ms)  : volt
        I_synE = ge * nS *         -v                           : amp
        I_synI = gi * nS * (-100.*mV-v)                          : amp
        dge/dt = -ge/(1.0*ms)                                   : 1
        dgi/dt = -gi/(2.0*ms)                                  : 1
        '''
if test_mode:
    neuron_eqs_e += '\n  theta      :volt'
else:
    neuron_eqs_e += '\n  dtheta/dt = -theta / (tc_theta)  : volt'

neuron_eqs_e += '\n  dtimer/dt = 100.0 : ms'

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

# STDP synaptic traces
eqs_stdp_ee = '''
            dpre/dt = -pre / tc_pre_ee : 1.0
            dpost/dt = -post / tc_post_ee : 1.0
            '''

# setting STDP update rule
if use_weight_dependence:
    if post_pre:
        eqs_stdp_pre_ee = 'pre = 1.; w -= nu_ee_pre * post * w ** exp_ee_pre'
        eqs_stdp_post_ee = 'w += nu_ee_post * pre * (wmax_ee - w) ** exp_ee_post; post = 1.'

    else:
        eqs_stdp_pre_ee = 'pre = 1.'
        eqs_stdp_post_ee = 'w += nu_ee_post * pre * (wmax_ee - w) ** exp_ee_post; post = 1.'

else:
    if post_pre:
        eqs_stdp_pre_ee = 'pre = 1.; w -= nu_ee_pre * post'
        eqs_stdp_post_ee = 'w += nu_ee_post * pre; post = 1.'

    else:
        eqs_stdp_pre_ee = 'pre = 1.'
        eqs_stdp_post_ee = 'w += nu_ee_post * pre; post = 1.'


b.ion()

fig_num = 1
neuron_groups = {}
input_groups = {}
connections = {}
input_connections = {}
stdp_methods = {}
rate_monitors = {}
spike_monitors = {}
spike_counters = {}

result_monitor = np.zeros((update_interval, n_e_full))

neuron_groups['e'] = b.NeuronGroup(n_e_total, neuron_eqs_e, threshold=v_thresh_e, refractory=refrac_e, reset=scr_e, compile=True, freeze=True)
neuron_groups['i'] = b.NeuronGroup(n_i_total, neuron_eqs_i, threshold=v_thresh_i, refractory=refrac_i, reset=v_reset_i, compile=True, freeze=True)

########################################################
# CREATE NETWORK POPULATIONS AND RECURRENT CONNECTIONS #
########################################################

for name in population_names:
    print '...creating neuron group:', name

    if name == 'CONV1':
        # get a subgroup of size 'n_e' from all exc neurons
        neuron_groups[name + 'e'] = neuron_groups['e'].subgroup(n_e_conv)
        # get a subgroup of size 'n_i' from the inhibitory layer
        neuron_groups[name + 'i'] = neuron_groups['i'].subgroup(n_i_conv)

    elif name == 'FULL1':
        # get a subgroup of size 'full_size' from all exc neurons
        neuron_groups[name + 'e'] = neuron_groups['e'].subgroup(n_e_full)
        # get a subgroup of size 'full_size' from all inhibitory neurons
        neuron_groups[name + 'i'] = neuron_groups['i'].subgroup(n_i_full)

    # start the membrane potentials of these groups 40mV below their resting potentials
    neuron_groups[name + 'e'].v = v_rest_e - 40. * b.mV
    neuron_groups[name + 'i'].v = v_rest_i - 40. * b.mV

print '...creating recurrent connections'

for name in population_names:
    # if we're in test mode / using some stored weights
    if test_mode or weight_path[-8:] == 'weights/conv_full_conn_weights/':
        # load up adaptive threshold parameters
        neuron_groups['e'].theta = np.load(weight_path + 'theta_A_' + stdp_input + '.npy')
    else:
        # otherwise, set the adaptive additive threshold parameter at 20mV
        neuron_groups['e'].theta = np.ones((n_e_total)) * 20.0 * b.mV

    if name == 'CONV1':
        for conn_type in recurrent_connection_types:
            if conn_type == 'ei':
                connection_name = name + conn_type[0] + name + conn_type[1] + '_' + ending
                # get the corresponding stored weights from file
                weight_matrix = get_matrix_from_file(data_path + 'random/conv_full_conn_random/' + connection_name + '.npy', n_src=conv_features * n_e_patch, n_tgt=conv_features)
                # create a connection from the first group in conn_name with the second group
                connections[connection_name] = b.Connection(neuron_groups[name + 'e'], neuron_groups[name + 'i'], structure='sparse', state='g' + conn_type[0])
                # instantiate the created connection with the 'weightMatrix' loaded from file
                for feature in xrange(conv_features):
                    for n in xrange(n_e_patch):
                        connections[connection_name][feature * n_e_patch + n, feature] = weight_matrix[feature * n_e_patch + n, feature]

            elif conn_type == 'ie':
                connection_name = name + conn_type[0] + name + conn_type[1] + '_' + ending
                # get the corresponding stored weights from file
                weight_matrix = get_matrix_from_file(data_path + 'random/conv_full_conn_random/' + connection_name + '.npy', n_src=conv_features, n_tgt=(conv_features ** 2) * n_e_patch)
                # create a connection from the first group in conn_name with the second group
                connections[connection_name] = b.Connection(neuron_groups[name + 'i'], neuron_groups[name + 'e'], structure='sparse', state='g' + conn_type[0])
                # instantiate the created connection with the 'weightMatrix' loaded from file
                for feature in xrange(conv_features):
                    for other_feature in xrange(conv_features):
                        if feature != other_feature:
                            for n in xrange(n_e_patch):
                                connections[connection_name][feature, other_feature * n_e_patch + n] = weight_matrix[feature, other_feature * n_e_patch + n]

    elif name == 'FULL1':
        for conn_type in recurrent_connection_types:
            if conn_type == 'ei':
                connection_name = name + conn_type[0] + name + conn_type[1] + '_' + ending
                # get the corresponding stored weights from file
                weight_matrix = get_matrix_from_file(data_path + 'random/conv_full_conn_random/' + connection_name + '.npy', n_src=n_e_full, n_tgt=n_e_full)
                # create a connection from the first group in conn_name with the second group
                connections[connection_name] = b.Connection(neuron_groups[name + 'e'], neuron_groups[name + 'i'], structure='dense', state='g' + conn_type[0])
                # instantiate the created connection with the 'weight_matrix' loaded from file
                connections[connection_name].connect(neuron_groups[name + 'e'], neuron_groups[name + 'i'], weight_matrix)

            elif conn_type == 'ie':
                # get the corresponding stored weights from file
                weight_matrix = get_matrix_from_file(data_path + 'random/conv_full_conn_random/' + connection_name + '.npy', n_src=n_e_full, n_tgt=n_e_full)
                # create a connection from the first group in conn_name with the second group
                connections[connection_name] = b.Connection(neuron_groups[name + 'i'], neuron_groups[name + 'e'], structure='dense', state='g' + conn_type[0])
                # instantiate the created connection with the 'weight_matrix' loaded from file
                connections[connection_name].connect(neuron_groups[name + 'i'], neuron_groups[name + 'e'], weight_matrix)

    # if STDP from excitatory -> excitatory is on and this connection is excitatory -> excitatory
    if ee_STDP_on and 'ee' in recurrent_connection_types:
        stdp_methods[name + 'e' + name + 'e'] = b.STDP(connections[name + 'e' + name + 'e'], eqs=eqs_stdp_ee, pre=eqs_stdp_pre_ee, post=eqs_stdp_post_ee, wmin=0., wmax=wmax_ee)

    print '...creating monitors for:', name

    # spike rate monitors for excitatory and inhibitory neuron populations
    rate_monitors[name + 'e'] = b.PopulationRateMonitor(neuron_groups[name + 'e'], bin=(single_example_time + resting_time) / b.second)
    rate_monitors[name + 'i'] = b.PopulationRateMonitor(neuron_groups[name + 'i'], bin=(single_example_time + resting_time) / b.second)
    spike_counters[name + 'e'] = b.SpikeCounter(neuron_groups[name + 'e'])

    # record neuron population spikes if specified
    if record_spikes:
        spike_monitors[name + 'e'] = b.SpikeMonitor(neuron_groups[name + 'e'])
        spike_monitors[name + 'i'] = b.SpikeMonitor(neuron_groups[name + 'i'])

if record_spikes and do_plot:
    b.figure(fig_num)
    fig_num += 1
    b.ion()
    b.subplot(211)
    b.raster_plot(spike_monitors['FULL1e'], refresh=1000 * b.ms, showlast=1000 * b.ms)
    b.subplot(212)
    b.raster_plot(spike_monitors['FULL1i'], refresh=1000 * b.ms, showlast=1000 * b.ms)
    
################################################################# 
# CREATE INPUT POPULATION AND CONNECTIONS FROM INPUT POPULATION #
#################################################################

# creating convolution locations inside the input image
convolution_locations = {}
for n in xrange(n_e_patch):
    convolution_locations[n] = [ ((n % n_e_patch_sqrt) * conv_stride + (n // n_e_patch_sqrt) * n_input_sqrt * conv_stride) + (x * n_input_sqrt) + y for y in xrange(conv_size) for x in xrange(conv_size) ]

# creating Poission spike train from input image (784 vector, 28x28 image)
for name in input_population_names:
    input_groups[name + 'e'] = b.PoissonGroup(n_input, 0)
    rate_monitors[name + 'e'] = b.PopulationRateMonitor(input_groups[name + 'e'], bin=(single_example_time + resting_time) / b.second)

# creating connections from input Poisson spike train to convolution patch populations
for name in input_connection_names:
    print '\n...creating connections between', name[:name.index('_')], 'and', name[name.index('_') + 1:]
    
    if name == 'X_CONV1':
        # for each of the input connection types (in this case, excitatory -> excitatory)
        for conn_type in input_connection_types:
            connection_name = name[:name.index('_')] + conn_type[0] + name[name.index('_') + 1:] + conn_type[1] + '_' + ending
            
            # get weight matrix depending on training or test phase
            if test_mode:
                weight_matrix = get_matrix_from_file(weight_path + connection_name + '_' + stdp_input + '.npy', n_src=n_input, n_tgt=conv_features * n_e_patch)
            else:
                weight_matrix = get_matrix_from_file(weight_path + connection_name + '.npy', n_src=n_input, n_tgt=conv_features * n_e_patch)

            # create connections from the windows of the input group to the neuron population
            input_connections[name] = b.Connection(input_groups['Xe'], neuron_groups[name[name.index('_') + 1:] + conn_type[1]], structure='sparse', state='g' + conn_type[0], delay=True, max_delay=delay[conn_type][1])
            
            # set the weights of this connection
            for feature in xrange(conv_features):
                for n in xrange(n_e_patch):
                    for idx in xrange(conv_size ** 2):
                        input_connections[name][convolution_locations[n][idx], feature * n_e_patch + n] = weight_matrix[convolution_locations[n][idx], feature * n_e_patch + n]

    elif name == 'CONV1_FULL1':
        # for each of the input connection types (in this case, excitatory -> excitatory)
        for conn_type in input_connection_types:
            connection_name = name[:name.index('_')] + conn_type[0] + name[name.index('_') + 1:] + conn_type[1] + '_' + ending
            
            # get weight matrix depending on training or test phase
            if test_mode:
                weight_matrix = get_matrix_from_file(weight_path + connection_name + '_' + stdp_input + '.npy', n_src=conv_features * n_e_patch, n_tgt=n_e_full)
            else:
                weight_matrix = get_matrix_from_file(weight_path + connection_name + '.npy', n_src=conv_features * n_e_patch, n_tgt=n_e_full)

            # create connections from the windows of the input group to the neuron population
            input_connections[name] = b.Connection(neuron_groups[name[:name.index('_')] + conn_type[0]], neuron_groups[name[name.index('_') + 1:] + conn_type[1]], structure='dense', state='g' + conn_type[0], delay=True, max_delay=delay[conn_type][1])
            
            # set the weights of the connection
            input_connections[name].connect(neuron_groups[name[:name.index('_')] + conn_type[0]], neuron_groups[name[name.index('_') + 1:] + conn_type[1]], weight_matrix)

    # if excitatory -> excitatory STDP is specified, add it here (input to excitatory populations)
    if ee_STDP_on:
        print '...creating STDP for connection', name
        
        # create the STDP object
        stdp_methods[name[:name.index('_')] + 'e' + name[name.index('_') + 1:] + 'e'] = b.STDP(input_connections[name], eqs=eqs_stdp_ee, pre=eqs_stdp_pre_ee, post=eqs_stdp_post_ee, wmin=0., wmax=wmax_ee)

print '\n'

#################################
# RUN SIMULATION AND SET INPUTS #
#################################

# bookkeeping variables
previous_conv_spike_count = np.zeros((n_input, conv_features * n_e_patch))
previous_fc_spike_count = np.zeros(n_e_full)
assignments = np.ones(n_e_full) * -1
input_numbers = [0] * num_examples
output_numbers = np.zeros((num_examples, 10))

# plot input weights
if not test_mode and do_plot:
    input_weight_monitor, fig_weights = plot_2d_input_weights()
    fig_num += 1
    conv_weight_monitor, fig2_weights = plot_2d_conv_weights()
    fig_num += 1

# plot input intensities
if do_plot:
    rates = np.zeros(n_input)
    input_image_monitor, input_image = plot_input()
    fig_num += 1

# plot performance
if do_plot_performance and do_plot:
    performance_monitor, performance, fig_num, fig_performance = plot_performance(fig_num)
else:
    performance = get_current_performance(np.zeros(int(num_examples / update_interval)), 0)

# set firing rates to zero initially
for name in input_population_names:
    input_groups[name + 'e'].rate = 0

# initialize network
j = 0
num_retries = 0
b.run(0)

weights_name = '_'.join(input_connection_names) + '_' + ending

# start recording time
start_time = timeit.default_timer()

while j < num_examples:
    # fetched rates depend on training / test phase, and whether we use the 
    # testing dataset for the test phase
    if test_mode:
        if use_testing_set:
            rates = testing['x'][j % 10000, :, :] / 8. * input_intensity
        else:
            rates = training['x'][j % 60000, :, :] / 8. * input_intensity
    
    else:
    	# ensure weights don't grow without bound
        normalize_weights()
        # get the firing rates of the next input example
        rates = training['x'][j % 60000, :, :] / 8. * input_intensity
    
    # plot the input at this step
    if do_plot:
        input_image_monitor = update_input(input_image_monitor, input_image)
    
    # sets the input firing rates
    input_groups['Xe'].rate = rates.reshape(n_input)
    
    # run the network for a single example time
    b.run(single_example_time)
    
    # get new neuron label assignments every 'update_interval'
    if j % update_interval == 0 and j > 0:
        assignments = get_new_assignments(result_monitor[:], input_numbers[j - update_interval : j])
    
    # get count of spikes over the past iteration
    current_conv_spike_count = np.copy(spike_counters['CONV1e'].count[:]).reshape((conv_features * n_e_patch)) - previous_conv_spike_count
    previous_conv_spike_count = np.copy(spike_counters['CONV1e'].count[:]).reshape((conv_features * n_e_patch))
    current_fc_spike_count = np.copy(spike_counters['FULL1e'].count[:]).reshape((n_e_full)) - previous_fc_spike_count
    previous_fc_spike_count = np.copy(spike_counters['FULL1e'].count[:]).reshape(n_e_full)
    
    # set weights to those of the most-fired neuron
    if not test_mode:
        set_weights_most_fired()

    # update weights every 'weight_update_interval'
    if j % weight_update_interval == 0 and not test_mode and do_plot:
        update_2d_input_weights(input_weight_monitor, fig_weights)
        update_2d_conv_weights(conv_weight_monitor, fig2_weights)
    
    # if the neurons in the network didn't spike more than four times
    if np.sum(current_fc_spike_count) + np.sum(current_conv_spike_count) < 5 and num_retries < 3:
        # increase the intensity of input
        input_intensity += 2
        num_retries += 1
        
        # set all network firing rates to zero
        for name in input_population_names:
            input_groups[name + 'e'].rate = 0

        # let the network relax back to equilibrium
        b.run(resting_time)
    # otherwise, record results and confinue simulation
    else:
        num_retries = 0
    	# record the current number of spikes
        result_monitor[j % update_interval, :] = current_fc_spike_count
        
        # decide whether to evaluate on test or training set
        if test_mode and use_testing_set:
            input_numbers[j] = testing['y'][j % 10000][0]
        else:
            input_numbers[j] = training['y'][j % 60000][0]
        
        # get the output classifications of the network
        output_numbers[j, :] = get_recognized_number_ranking(assignments, result_monitor[j % update_interval, :])
        
        # print progress
        if j % print_progress_interval == 0 and j > 0:
            print 'runs done:', j, 'of', int(num_examples), '(time taken for past', print_progress_interval, 'runs:', str(timeit.default_timer() - start_time) + ')'
            start_time = timeit.default_timer()
        
        # plot performance if appropriate
        if j % update_interval == 0 and j > 0:
            if do_plot_performance and do_plot:
                # updating the performance plot
                perf_plot, performance = update_performance_plot(performance_monitor, performance, j, fig_performance)
            else:
                performance = get_current_performance(performance, j)
            # printing out classification performance results so far
            print '\nClassification performance', performance[:int(j / float(update_interval)) + 1], '\n'
            target = open('../performance/conv_full_conn_performance/' + weights_name + '_' + stdp_input + '.txt', 'w')
            target.truncate()
            target.write('Iteration ' + str(j) + '\n')
            target.write(str(performance[:int(j / float(update_interval)) + 1]))
            target.close()
                
        # set input firing rates back to zero
        for name in input_population_names:
            input_groups[name + 'e'].rate = 0
        
        # run the network for 'resting_time' to relax back to rest potentials
        b.run(resting_time)
        # reset the input firing intensity
        input_intensity = start_input_intensity
        # increment the example counter
        j += 1

# set weights to those of the most-fired neuron
if not test_mode:
    set_weights_most_fired()

################ 
# SAVE RESULTS #
################ 

print '...saving results'

if not test_mode:
    save_theta()
if not test_mode:
    save_connections()
else:
    np.save(data_path + 'activity/conv_full_conn_activity/resultPopVecs' + str(num_examples) + '_' + stdp_input + '_' + ending, result_monitor)
    np.save(data_path + 'activity/conv_full_conv_activity/inputNumbers' + str(num_examples) + '_' + stdp_input + '_' + ending, input_numbers)

################ 
# PLOT RESULTS #
################

if do_plot:
    if rate_monitors:
        b.figure(fig_num)
        fig_num += 1
        for i, name in enumerate(rate_monitors):
            b.subplot(len(rate_monitors), 1, i + 1)
            b.plot(rate_monitors[name].times / b.second, rate_monitors[name].rate, '.')
            b.title('Rates of population ' + name)

    if spike_monitors:
        b.figure(fig_num)
        fig_num += 1
        for i, name in enumerate(spike_monitors):
            b.subplot(len(spike_monitors), 1, i + 1)
            b.raster_plot(spike_monitors[name])
            b.title('Spikes of population ' + name)
            
    if spike_counters:
        b.figure(fig_num)
        fig_num += 1
        for i, name in enumerate(spike_counters):
            b.subplot(len(spike_counters), 1, i + 1)
            b.plot(np.asarray(spike_counters['Ae'].count[:]))
            b.title('Spike count of population ' + name)

    plot_2d_input_weights()

    b.ioff()
    b.show()
