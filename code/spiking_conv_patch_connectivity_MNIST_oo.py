'''
Much the same as 'spiking_MNIST.py', but we instead use a number of convolutional
windows to map the input to a reduced space.
'''

import numpy as np
import matplotlib.cm as cmap
import cPickle as p
import brian_no_units
import brian as b
import time, os.path, scipy, math, sys, timeit, random, argparse

from scipy.sparse import coo_matrix
from struct import unpack
from brian import *

np.set_printoptions(threshold=np.nan)

# only show log messages of level ERROR or higher
b.log_level_error()

MNIST_data_path = '../data/'
top_level_path = '../'


def get_labeled_data(picklename, b_train=True):
	'''
	Read input-vector (image) and target class (label, 0-9) and return it as 
	a list of tuples.
	'''
	if os.path.isfile('%s.pickle' % picklename):
		data = p.load(open('%s.pickle' % picklename))
	else:
		# Open the images with gzip in read binary mode
		if b_train:
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
				print("i: %i" % i)
			x[i] = [[unpack('>B', images.read(1))[0] for unused_col in xrange(cols)]  for unused_row in xrange(rows) ]
			y[i] = unpack('>B', labels.read(1))[0]

		data = {'x': x, 'y': y, 'rows': rows, 'cols': cols}
		p.dump(data, open("%s.pickle" % picklename, "wb"))
	return data


def is_lattice_connection(sqrt, i, j):
	'''
	Boolean method which checks if two indices in a network correspond to neighboring nodes in a 4-, 8-, or all-lattice.

	sqrt: square root of the number of nodes in population
	i: First neuron's index
	k: Second neuron's index
	'''
	if lattice_structure == 'none':
		return False
	if lattice_structure == '4':
		return i + 1 == j and j % sqrt != 0 or i - 1 == j and i % sqrt != 0 or i + sqrt == j or i - sqrt == j
	if lattice_structure == '8':
		return i + 1 == j and j % sqrt != 0 or i - 1 == j and i % sqrt != 0 or i + sqrt == j or i - sqrt == j or i + sqrt == j + 1 and j % sqrt != 0 or i + sqrt == j - 1 and i % sqrt != 0 or i - sqrt == j + 1 and i % sqrt != 0 or i - sqrt == j - 1 and j % sqrt != 0
	if lattice_structure == 'all':
		return True

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
	print '...saving connections: weights/conv_patch_connectivity_weights/' + save_conns[0] + '_' + ending + ' and ' + 'weights/conv_patch_connectivity_weights/' + save_conns[1] + '_' + stdp_input

	# iterate over all connections to save
	for conn_name in save_conns:
		if conn_name == 'AeAe':
			conn_matrix = connections[conn_name][:]
		else:
			conn_matrix = input_connections[conn_name][:]
		# sparsify it into (row, column, entry) tuples
		conn_list_sparse = ([(i, j, conn_matrix[i, j]) for i in xrange(conn_matrix.shape[0]) for j in xrange(conn_matrix.shape[1]) ])
		# save it out to disk
		np.save(top_level_path + 'weights/conv_patch_connectivity_weights/' + conn_name + '_' + ending, conn_list_sparse)


def save_theta():
	'''
	Save the adaptive threshold parameters to a file.
	'''

	# iterate over population for which to save theta parameters
	for pop_name in population_names:
		# print out saved theta populations
		print '...saving theta: weights/conv_patch_connectivity_weights/theta_' + pop_name + '_' + ending

		# save out the theta parameters to file
		np.save(top_level_path + 'weights/conv_patch_connectivity_weights/theta_' + pop_name + '_' + ending, neuron_groups[pop_name + 'e'].theta)


def set_weights_most_fired():
	'''
	For each convolutional patch, set the weights to those of the neuron which
	fired the most in the last iteration.
	'''
	for conn_name in input_connections:
		for feature in xrange(conv_features):
			# count up the spikes for the neurons in this convolution patch
			column_sums = np.sum(current_spike_count[feature : feature + 1, :], axis=0)

			# find the excitatory neuron which spiked the most
			most_spiked = np.argmax(column_sums)

			# create a "dense" version of the most spiked excitatory neuron's weight
			most_spiked_dense = input_connections[conn_name][:, feature * n_e + most_spiked].todense()

			# set all other neurons' (in the same convolution patch) weights the same as the most-spiked neuron in the patch
			for n in xrange(n_e):
				if n != most_spiked:
					other_dense = input_connections[conn_name][:, feature * n_e + n].todense()
					other_dense[convolution_locations[n]] = most_spiked_dense[convolution_locations[most_spiked]]
					input_connections[conn_name][:, feature * n_e + n] = other_dense


def normalize_weights():
	'''
	Squash the input -> excitatory weights to sum to a prespecified number.
	'''
	for conn_name in input_connections:
		connection = input_connections[conn_name][:].todense()
		for feature in xrange(conv_features):
			feature_connection = connection[:, feature * n_e : (feature + 1) * n_e]
			column_sums = np.sum(feature_connection, axis=0)
			column_factors = weight['ee_input'] / column_sums

			for n in xrange(n_e):
				dense_weights = input_connections[conn_name][:, feature * n_e + n].todense()
				dense_weights[convolution_locations[n]] *= column_factors[n]
				input_connections[conn_name][:, feature * n_e + n] = dense_weights

	for conn_name in connections:
		if 'AeAe' in conn_name and lattice_structure != 'none':
			connection = connections[conn_name][:].todense()
			for feature in xrange(conv_features):
				feature_connection = connection[feature * n_e : (feature + 1) * n_e, :]
				column_sums = np.sum(feature_connection)
				column_factors = weight['ee_recurr'] / column_sums

				for idx in xrange(feature * n_e, (feature + 1) * n_e):
					connections[conn_name][idx, :] *= column_factors


def plot_input(rates):
	'''
	Plot the current input example during the training procedure.
	'''
	fig = b.figure(fig_num, figsize = (5, 5))
	im = b.imshow(rates.reshape((28, 28)), interpolation = 'nearest', vmin=0, vmax=64, cmap=cmap.get_cmap('gray'))
	b.colorbar(im)
	b.title('Current input example')
	fig.canvas.draw()
	return im, fig


def update_input(rates, im, fig):
	'''
	Update the input image to use for input plotting.
	'''
	im.set_array(rates.reshape((28, 28)))
	fig.canvas.draw()
	return im


def get_2d_input_weights():
	'''
	Get the weights from the input to excitatory layer and reshape it to be two
	dimensional and square.
	'''
	rearranged_weights = np.zeros((conv_features_sqrt * conv_size * n_e_sqrt, conv_features_sqrt * conv_size * n_e_sqrt))
	connection = input_connections['XeAe'][:]

	# for each convolution feature
	for feature in xrange(conv_features):
		# for each excitatory neuron in this convolution feature
		for n in xrange(n_e):
			temp = connection[:, feature * n_e + (n // n_e_sqrt) * n_e_sqrt + (n % n_e_sqrt)].todense()

			# print ((feature // conv_features_sqrt) * conv_size * n_e_sqrt) + ((n // n_e_sqrt) * conv_size), ((feature // conv_features_sqrt) * conv_size * n_e_sqrt) + ((n // n_e_sqrt) * conv_size) + conv_size, ((feature % conv_features_sqrt) * conv_size * n_e_sqrt) + ((n % n_e_sqrt) * (conv_size)), ((feature % conv_features_sqrt) * conv_size * n_e_sqrt) + ((n % n_e_sqrt) * (conv_size)) + conv_size

			rearranged_weights[ ((feature // conv_features_sqrt) * conv_size * n_e_sqrt) + ((n // n_e_sqrt) * conv_size) : ((feature // conv_features_sqrt) * conv_size * n_e_sqrt) + ((n // n_e_sqrt) * conv_size) + conv_size, ((feature % conv_features_sqrt) * conv_size * n_e_sqrt) + ((n % n_e_sqrt) * (conv_size)) : ((feature % conv_features_sqrt) * conv_size * n_e_sqrt) + ((n % n_e_sqrt) * (conv_size)) + conv_size ] = temp[convolution_locations[n]].reshape((conv_size, conv_size))

	# return the rearranged weights to display to the user
	return rearranged_weights.T


def plot_2d_input_weights():
	'''
	Plot the weights from input to excitatory layer to view during training.
	'''
	weights = get_2d_input_weights()
	fig = b.figure(fig_num, figsize=(18, 18))
	im = b.imshow(weights, interpolation='nearest', vmin=0, vmax=wmax_ee, cmap=cmap.get_cmap('hot_r'))
	for idx in xrange(conv_size * n_e_sqrt, conv_size * conv_features_sqrt * n_e_sqrt, conv_size * n_e_sqrt):
		b.axvline(idx, ls='--', lw=1)
		b.axhline(idx, ls='--', lw=1)
	b.colorbar(im)
	b.title('Reshaped input -> convolution weights')
	b.xticks(xrange(0, conv_size * conv_features_sqrt * n_e_sqrt, conv_size * n_e_sqrt))
	b.yticks(xrange(0, conv_size * conv_features_sqrt * n_e_sqrt, conv_size * n_e_sqrt))
	fig.canvas.draw()
	return im, fig


def update_2d_input_weights(im, fig):
	'''
	Update the plot of the weights from input to excitatory layer to view during training.
	'''
	weights = get_2d_input_weights()
	im.set_array(weights)
	fig.canvas.draw()
	return im


def get_patch_weights():
	'''
	Get the weights from the input to excitatory layer and reshape them.
	'''
	rearranged_weights = np.zeros((conv_features * n_e, conv_features * n_e))
	connection = connections['AeAe'][:]

	for feature in xrange(conv_features):
		for other_feature in xrange(conv_features):
			if feature != other_feature:
				for this_n in xrange(n_e):
					for other_n in xrange(n_e):
						if is_lattice_connection(n_e_sqrt, this_n, other_n):
							rearranged_weights[feature * n_e + this_n, other_feature * n_e + other_n] = connection[feature * n_e + this_n, other_feature * n_e + other_n]

	return rearranged_weights


def plot_patch_weights():
	'''
	Plot the weights between convolution patches to view during training.
	'''
	weights = get_patch_weights()
	fig = b.figure(fig_num, figsize=(8, 8))
	im = b.imshow(weights, interpolation='nearest', vmin=0, vmax=wmax_ee, cmap=cmap.get_cmap('hot_r'))
	for idx in xrange(n_e, n_e * conv_features, n_e):
		b.axvline(idx, ls='--', lw=1)
		b.axhline(idx, ls='--', lw=1)
	b.colorbar(im)
	b.title('Between-patch connectivity')
	fig.canvas.draw()
	return im, fig

def update_patch_weights(im, fig):
	'''
	Update the plot of the weights between convolution patches to view during training.
	'''
	weights = get_patch_weights()
	im.set_array(weights)
	fig.canvas.draw()
	return im

def plot_neuron_votes(assignments, spike_rates):
	'''
	Plot the votes of the neurons per label.
	'''
	all_summed_rates = [0] * 10
	num_assignments = [0] * 10

	for i in xrange(10):
		num_assignments[i] = len(np.where(assignments == i)[0])
		if num_assignments[i] > 0:
			all_summed_rates[i] = np.sum(spike_rates[assignments == i]) / num_assignments[i]

	fig = b.figure(fig_num, figsize=(8, 8))
	im = b.bar(xrange(10), all_summed_rates)
	b.title('Votes per label')
	fig.canvas.draw()
	return im, fig


def update_neuron_votes(im, fig):
	'''
	Update the plot of the votes of the neurons by label.
	'''
	all_summed_rates = [0] * 10
	num_assignments = [0] * 10

	for i in xrange(10):
		num_assignments[i] = len(np.where(assignments == i)[0])
		if num_assignments[i] > 0:
			all_summed_rates[i] = np.sum(rates[assignments == i]) / num_assignments[i]

	for rect, h in zip():
		im.set_height(xrange(10), all_summed_rates)
	fig.canvas.draw()
	return im


def get_current_performance(all_performance, most_spiked_performance, top_percent_performance, current_example_num):
	'''
	Evaluate the performance of the network on the past 'update_interval' training
	examples.
	'''
	global all_output_numbers, most_spiked_output_numbers, top_percent_output_numbers, input_numbers

	current_evaluation = int(current_example_num / update_interval)
	start_num = current_example_num - update_interval
	end_num = current_example_num
	
	all_difference = all_output_numbers[start_num:end_num, 0] - input_numbers[start_num:end_num]
	all_correct = len(np.where(all_difference == 0)[0])
	
	most_spiked_difference = most_spiked_output_numbers[start_num:end_num, 0] - input_numbers[start_num:end_num]
	most_spiked_correct = len(np.where(most_spiked_difference == 0)[0])

	top_percent_difference = top_percent_output_numbers[start_num:end_num, 0] - input_numbers[start_num:end_num]
	top_percent_correct = len(np.where(top_percent_difference == 0)[0])

	all_performance[current_evaluation] = all_correct / float(update_interval) * 100
	most_spiked_performance[current_evaluation] = most_spiked_correct / float(update_interval) * 100
	top_percent_performance[current_evaluation] = top_percent_correct / float(update_interval) * 100
	
	return all_performance, most_spiked_performance, top_percent_performance


def plot_performance(fig_num):
	'''
	Set up the performance plot for the beginning of the simulation.
	'''
	num_evaluations = int(num_examples / update_interval)
	time_steps = range(0, num_evaluations)

	all_performance = np.zeros(num_evaluations)
	most_spiked_performance = np.zeros(num_evaluations)
	top_percent_performance = np.zeros(num_evaluations)

	fig = b.figure(fig_num, figsize = (15, 5))
	fig_num += 1
	ax = fig.add_subplot(111)

	im, = ax.plot(time_steps, all_performance)
	im, = ax.plot(time_steps, most_spiked_performance)
	im, = ax.plot(time_steps, top_percent_performance)

	b.ylim(ymax = 100)
	b.title('Classification performance')
	fig.canvas.draw()

	return im, all_performance, most_spiked_performance, top_percent_performance, fig_num, fig


def update_performance_plot(im, all_performance, most_spiked_performance, top_percent_performance, current_example_num, fig):
	'''
	Update the plot of the performance based on results thus far.
	'''
	all_performance, most_spiked_performance, top_percent_performance = get_current_performance(all_performance, most_spiked_performance, top_percent_performance, current_example_num)
	im.set_ydata(all_performance)
	im.set_ydata(most_spiked_performance)
	im.set_ydata(top_percent_performance)
	fig.canvas.draw()
	return im, all_performance, most_spiked_performance, top_percent_performance


def get_recognized_number_ranking(assignments, spike_rates):
	'''
	Given the label assignments of the excitatory layer and their spike rates over
	the past 'update_interval', get the ranking of each of the categories of input.
	'''
	most_spiked_summed_rates = [0] * 10
	num_assignments = [0] * 10

	most_spiked_array = np.array(np.zeros((conv_features, n_e)), dtype=bool)

	for feature in xrange(conv_features):
		# count up the spikes for the neurons in this convolution patch
		column_sums = np.sum(spike_rates[feature : feature + 1, :], axis=0)

		# find the excitatory neuron which spiked the most
		most_spiked_array[feature, np.argmax(column_sums)] = True

	# for each label
	for i in xrange(10):
		# get the number of label assignments of this type
		num_assignments[i] = len(np.where(assignments[most_spiked_array] == i)[0])

		if len(spike_rates[np.where(assignments[most_spiked_array] == i)]) > 0:
			# sum the spike rates of all excitatory neurons with this label, which fired the most in its patch
			most_spiked_summed_rates[i] = np.sum(spike_rates[np.where(np.logical_and(assignments == i, most_spiked_array))]) / float(np.sum(spike_rates[most_spiked_array]))

	all_summed_rates = [0] * 10
	num_assignments = [0] * 10

	for i in xrange(10):
		num_assignments[i] = len(np.where(assignments == i)[0])
		if num_assignments[i] > 0:
			all_summed_rates[i] = np.sum(spike_rates[assignments == i]) / num_assignments[i]

	top_percent_votes = [0] * 10
	num_assignments = [0] * 10
	
	top_percent_array = np.array(np.zeros((conv_features, n_e)), dtype=bool)
	top_percent_array[np.where(spike_rates > np.percentile(spike_rates, 100 - top_percent))] = True

	# for each label
	for i in xrange(10):
		# get the number of label assignments of this type
		num_assignments[i] = len(np.where(assignments[top_percent_array] == i)[0])

		if len(np.where(assignments[top_percent_array] == i)) > 0:
			# sum the spike rates of all excitatory neurons with this label, which fired the most in its patch
			top_percent_votes[i] = len(spike_rates[np.where(np.logical_and(assignments == i, top_percent_array))])


	return np.argsort(all_summed_rates)[::-1], np.argsort(most_spiked_summed_rates)[::-1], np.argsort(top_percent_votes)[::-1]


def get_new_assignments(result_monitor, input_numbers):
	'''
	Based on the results from the previous 'update_interval', assign labels to the
	excitatory neurons.
	'''
	assignments = np.ones((conv_features, n_e))
	input_nums = np.asarray(input_numbers)
	maximum_rate = np.zeros(conv_features * n_e)
	
	for j in xrange(10):
		num_assignments = len(np.where(input_nums == j)[0])
		if num_assignments > 0:
			rate = np.sum(result_monitor[input_nums == j], axis=0) / num_assignments
			for i in xrange(conv_features * n_e):
				if rate[i // n_e, i % n_e] > maximum_rate[i]:
					maximum_rate[i] = rate[i // n_e, i % n_e]
					assignments[i // n_e, i % n_e] = j
	
	return assignments


class ConvolutionalSpikingNN(object):
	'''
	Object which represents a convolutional spiking neural network.
	'''

	def __init__(mode, connectivity, weight_dependence, post_pre, conv_size, conv_stride, conv_features, weight_sharing, lattice_structure, random_inhibition_prob, top_percent):
		'''
		Network initialization.
		'''

		# setting input parameters
		this.mode = mode
		this.connectivity = connectivity
		this.weight_dependence = weight_dependence
		this.post_pre = post_pre
		this.conv_size = conv_size
		this.conv_features = conv_features
		this.weight_sharing = weight_sharing
		this.lattice_structure = lattice_structure
		this.random_inhibition_prob = random_inhibition_prob

		# load training or testing data
		if mode == 'train':
		    start = time.time()
		    this.data = get_labeled_data(MNIST_data_path + 'training')
		    end = time.time()
		    print 'time needed to load training set:', end - start
		else:
		    start = time.time()
		    this.data = get_labeled_data(MNIST_data_path + 'testing', bTrain = False)
		    end = time.time()
		    print 'time needed to load test set:', end - start

		# set parameters for simulation based on train / test mode
		if test_mode:
			weight_path = top_level_path + 'weights/conv_patch_connectivity_weights/'
			this.num_examples = 10000 * 1
			this.do_plot_performance = False
			ee_STDP_on = False
		else:
			weight_path = top_level_path + 'random/conv_patch_connectivity_random/'
			this.num_examples = 60000 * 1
			this.do_plot_performance = True
			ee_STDP_on = True

		# plotting or not
		do_plot = True

		# number of inputs to the network
		this.n_input = 784
		this.n_input_sqrt = int(math.sqrt(n_input))

		# number of neurons parameters
		this.n_e = ((n_input_sqrt - conv_size) / conv_stride + 1) ** 2
		this.n_e_total = n_e * conv_features
		this.n_e_sqrt = int(math.sqrt(n_e))
		this.n_i = n_e
		this.conv_features_sqrt = int(math.sqrt(conv_features))

		# time (in seconds) per data example presentation and rest period in between, used to calculate total runtime
		this.single_example_time = 0.35 * b.second
		this.resting_time = 0.15 * b.second
		runtime = num_examples * (single_example_time + resting_time)

		# set the update interval
		if test_mode:
			this.update_interval = num_examples
		else:
			this.update_interval = 100

		# rest potential parameters, reset potential parameters, threshold potential parameters, and refractory periods
		v_rest_e, v_rest_i = -65. * b.mV, -60. * b.mV
		v_reset_e, v_reset_i = -65. * b.mV, -45. * b.mV
		v_thresh_e, v_thresh_i = -52. * b.mV, -40. * b.mV
		refrac_e, refrac_i = 5. * b.ms, 2. * b.ms

		# dictionaries for weights and delays
		weight, delay = {}, {}

		# populations, connections, saved connections, etc.
		input_population_names = [ 'X' ]
		population_names = [ 'A' ]
		input_connection_names = [ 'XA' ]
		save_conns = [ 'XeAe', 'AeAe' ]

		# weird and bad names for variables, I think
		input_conn_names = [ 'ee_input' ]
		recurrent_conn_names = [ 'ei', 'ie', 'ee' ]
		
		# setting weight, delay, and intensity parameters
		weight['ee_input'] = (conv_size ** 2) * 0.175
		delay['ee_input'] = (0 * b.ms, 10 * b.ms)
		delay['ei_input'] = (0 * b.ms, 5 * b.ms)
		input_intensity = start_input_intensity = 2.0

		# time constants, learning rates, max weights, weight dependence, etc.
		tc_pre_ee, tc_post_ee = 20 * b.ms, 20 * b.ms
		nu_ee_pre, nu_ee_post = 0.0001, 0.01
		wmax_ee = 1.0
		exp_ee_post = exp_ee_pre = 0.2
		w_mu_pre, w_mu_post = 0.2, 0.2

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

		# creating dictionaries for various objects
		this.neuron_groups = {}
		this.input_groups = {}
		this.connections = {}
		this.input_connections = {}
		this.stdp_methods = {}
		this.rate_monitors = {}
		this.spike_monitors = {}
		this.spike_counters = {}

		# creating excitatory, inhibitory populations
		this.neuron_groups['e'] = b.NeuronGroup(n_e_total, neuron_eqs_e, threshold=v_thresh_e, refractory=refrac_e, reset=scr_e, compile=True, freeze=True)
		this.neuron_groups['i'] = b.NeuronGroup(n_e_total, neuron_eqs_i, threshold=v_thresh_i, refractory=refrac_i, reset=v_reset_i, compile=True, freeze=True)

		# creating subpopulations of excitatory, inhibitory neurons
		for name in population_names:
			print '...creating neuron group:', name

			# get a subgroup of size 'n_e' from all exc
			neuron_groups[name + 'e'] = neuron_groups['e'].subgroup(conv_features * n_e)
			# get a subgroup of size 'n_i' from the inhibitory layer
			neuron_groups[name + 'i'] = neuron_groups['i'].subgroup(conv_features * n_e)

			# start the membrane potentials of these groups 40mV below their resting potentials
			neuron_groups[name + 'e'].v = v_rest_e - 40. * b.mV
			neuron_groups[name + 'i'].v = v_rest_i - 40. * b.mV

		print '...creating recurrent connections'

		for name in population_names:
			# if we're in test mode / using some stored weights
			if mode == 'test' or weight_path[-8:] == 'weights/conv_patch_connectivity_weights/':
				# load up adaptive threshold parameters
				neuron_groups['e'].theta = np.load(weight_path + 'theta_A' + '_' + ending +'.npy')
			else:
				# otherwise, set the adaptive additive threshold parameter at 20mV
				neuron_groups['e'].theta = np.ones((n_e_total)) * 20.0 * b.mV

			for conn_type in recurrent_conn_names:
				if conn_type == 'ei':
					# create connection name (composed of population and connection types)
					conn_name = name + conn_type[0] + name + conn_type[1]
					# create a connection from the first group in conn_name with the second group
					connections[conn_name] = b.Connection(neuron_groups[conn_name[0:2]], neuron_groups[conn_name[2:4]], structure='sparse', state='g' + conn_type[0])
					# instantiate the created connection
					for feature in xrange(conv_features):
						for n in xrange(n_e):
							connections[conn_name][feature * n_e + n, feature * n_e + n] = 10.4

				elif conn_type == 'ie':
					# create connection name (composed of population and connection types)
					conn_name = name + conn_type[0] + name + conn_type[1]
					# create a connection from the first group in conn_name with the second group
					connections[conn_name] = b.Connection(neuron_groups[conn_name[0:2]], neuron_groups[conn_name[2:4]], structure='sparse', state='g' + conn_type[0])
					# instantiate the created connection
					for feature in xrange(conv_features):
						for other_feature in xrange(conv_features):
							if feature != other_feature:
								for n in xrange(n_e):
									connections[conn_name][feature * n_e + n, other_feature * n_e + n] = 17.4

					if random_inhibition_prob != 0.0:
						for feature in xrange(conv_features):
							for other_feature in xrange(conv_features):
								for n_this in xrange(n_e):
									for n_other in xrange(n_e):
										if n_this != n_other:
											if b.random() < random_inhibition_prob:
												connections[conn_name][feature * n_e + n_this, other_feature * n_e + n_other] = 17.4

				elif conn_type == 'ee':
					# create connection name (composed of population and connection types)
					conn_name = name + conn_type[0] + name + conn_type[1]
					# get weights from file if we are in test mode
					if mode == 'test':
						weight_matrix = get_matrix_from_file(weight_path + conn_name + '_' + ending + '.npy', conv_features * n_e, conv_features * n_e)
					# create a connection from the first group in conn_name with the second group
					connections[conn_name] = b.Connection(neuron_groups[conn_name[0:2]], neuron_groups[conn_name[2:4]], structure='sparse', state='g' + conn_type[0])
					# instantiate the created connection
					if connectivity == 'all':
						for feature in xrange(conv_features):
							for other_feature in xrange(conv_features):
								if feature != other_feature:
									for this_n in xrange(n_e):
										for other_n in xrange(n_e):
											if is_lattice_connection(n_e_sqrt, this_n, other_n):
												if mode == 'test':
													connections[conn_name][feature * n_e + this_n, other_feature * n_e + other_n] = weight_matrix[feature * n_e + this_n, other_feature * n_e + other_n]
												else:
													connections[conn_name][feature * n_e + this_n, other_feature * n_e + other_n] = (b.random() + 0.01) * 0.3

					elif connectivity == 'pairs':
						for feature in xrange(conv_features):
							if feature % 2 == 0:
								for this_n in xrange(n_e):
									for other_n in xrange(n_e):
										if is_lattice_connection(n_e_sqrt, this_n, other_n):
											if mode == 'test':
												connections[conn_name][feature * n_e + this_n, (feature + 1) * n_e + other_n] = weight_matrix[feature * n_e + this_n, (feature + 1) * n_e + other_n]
											else:
												connections[conn_name][feature * n_e + this_n, (feature + 1) * n_e + other_n] = (b.random() + 0.01) * 0.3
							elif feature % 2 == 1:
								for this_n in xrange(n_e):
									for other_n in xrange(n_e):
										if is_lattimode == 'test'ce_connection(n_e_sqrt, this_n, other_n):
											if mode == 'test':
												connections[conn_name][feature * n_e + this_n, (feature - 1) * n_e + other_n] = weight_matrix[feature * n_e + this_n, (feature - 1) * n_e + other_n]
											else:
												connections[conn_name][feature * n_e + this_n, (feature - 1) * n_e + other_n] = (b.random() + 0.01) * 0.3

					elif connectivity == 'none':
						pass

			# if STDP from excitatory -> excitatory is on and this connection is excitatory -> excitatory
			if ee_STDP_on and 'ee' in recurrent_conn_names:
				stdp_methods[name + 'e' + name + 'e'] = b.STDP(connections[name + 'e' + name + 'e'], eqs=eqs_stdp_ee, pre=eqs_stdp_pre_ee, post=eqs_stdp_post_ee, wmin=0., wmax=wmax_ee)

			print '...creating monitors for:', name

			# spike rate monitors for excitatory and inhibitory neuron populations
			rate_monitors[name + 'e'] = b.PopulationRateMonitor(neuron_groups[name + 'e'], bin=(single_example_time + resting_time) / b.second)
			rate_monitors[name + 'i'] = b.PopulationRateMonitor(neuron_groups[name + 'i'], bin=(single_example_time + resting_time) / b.second)
			spike_counters[name + 'e'] = b.SpikeCounter(neuron_groups[name + 'e'])

			# record neuron population spikes if specified
			spike_monitors[name + 'e'] = b.SpikeMonitor(neuron_groups[name + 'e'])
			spike_monitors[name + 'i'] = b.SpikeMonitor(neuron_groups[name + 'i'])

		if do_plot:
			b.figure(fig_num)
			fig_num += 1
			b.ion()
			b.subplot(211)
			b.raster_plot(spike_monitors['Ae'], refresh=1000 * b.ms, showlast=1000 * b.ms)
			b.subplot(212)
			b.raster_plot(spike_monitors['Ai'], refresh=1000 * b.ms, showlast=1000 * b.ms)

		# creating lattice locations for each patch
		if connectivity == 'all':
			lattice_locations = {}
			for this_n in xrange(conv_features * n_e):
				lattice_locations[this_n] = [ other_n for other_n in xrange(conv_features * n_e) if is_lattice_connection(n_e_sqrt, this_n % n_e, other_n % n_e) ]
		elif connectivity == 'pairs':
			lattice_locations = {}
			for this_n in xrange(conv_features * n_e):
				lattice_locations[this_n] = []
				for other_n in xrange(conv_features * n_e):
					if this_n // n_e % 2 == 0:
						if is_lattice_connection(n_e_sqrt, this_n % n_e, other_n % n_e) and other_n // n_e == this_n // n_e + 1:
							lattice_locations[this_n].append(other_n)
					elif this_n // n_e % 2 == 1:
						if is_lattice_connection(n_e_sqrt, this_n % n_e, other_n % n_e) and other_n // n_e == this_n // n_e - 1:
							lattice_locations[this_n].append(other_n)
		elif connectivity == 'none':
			lattice_locations = {}

		# setting up parameters for weight normalization between patches
		num_lattice_connections = sum([ len(value) for value in lattice_locations.values() ])
		weight['ee_recurr'] = (num_lattice_connections / conv_features) * 0.15

		# creating Poission spike train from input image (784 vector, 28x28 image)
		for name in input_population_names:
			input_groups[name + 'e'] = b.PoissonGroup(n_input, 0)
			rate_monitors[name + 'e'] = b.PopulationRateMonitor(input_groups[name + 'e'], bin=(single_example_time + resting_time) / b.second)

		# creating connections from input Poisson spike train to convolution patch populations
		for name in input_connection_names:
			print '\n...creating connections between', name[0], 'and', name[1]
			
			# for each of the input connection types (in this case, excitatory -> excitatory)
			for conn_type in input_conn_names:
				# saved connection name
				conn_name = name[0] + conn_type[0] + name[1] + conn_type[1]

				# get weight matrix depending on training or test phase
				if mode == 'test':
					weight_matrix = get_matrix_from_file(weight_path + conn_name + '_' + ending + '.npy', n_input, conv_features * n_e)

				# create connections from the windows of the input group to the neuron population
				input_connections[conn_name] = b.Connection(input_groups['Xe'], neuron_groups[name[1] + conn_type[1]], structure='sparse', state='g' + conn_type[0], delay=True, max_delay=delay[conn_type][1])
				
				if mode == 'test':
					for feature in xrange(conv_features):
						for n in xrange(n_e):
							for idx in xrange(conv_size ** 2):
								input_connections[conn_name][convolution_locations[n][idx], feature * n_e + n] = weight_matrix[convolution_locations[n][idx], feature * n_e + n]
				else:
					for feature in xrange(conv_features):
						for n in xrange(n_e):
							for idx in xrange(conv_size ** 2):
								input_connections[conn_name][convolution_locations[n][idx], feature * n_e + n] = (b.random() + 0.01) * 0.3

			# if excitatory -> excitatory STDP is specified, add it here (input to excitatory populations)
			if ee_STDP_on:
				print '...creating STDP for connection', name
				
				# STDP connection name
				conn_name = name[0] + conn_type[0] + name[1] + conn_type[1]
				# create the STDP object
				stdp_methods[conn_name] = b.STDP(input_connections[conn_name], eqs=eqs_stdp_ee, pre=eqs_stdp_pre_ee, post=eqs_stdp_post_ee, wmin=0., wmax=wmax_ee)

		print '\n'

	def run_simulation():

		# weight updates and progress printing intervals
		weight_update_interval = 10
		print_progress_interval = 10

		# plot input weights
		if not test_mode and do_plot:
			input_weight_monitor, fig_weights = plot_2d_input_weights()
			fig_num += 1
			patch_weight_monitor, fig2_weights = plot_patch_weights()
			fig_num += 1
			# neuron_vote_monitor, fig_neuron_votes = plot_neuron_votes(assignments, rates)
			# fig_num += 1

		# plot input intensities
		if do_plot:
			input_image_monitor, input_image = plot_input(rates)
			fig_num += 1

		# plot performance
		if do_plot_performance and do_plot:
			performance_monitor, all_performance, most_spiked_performance, top_percent_performance, fig_num, fig_performance = plot_performance(fig_num)
		else:
			all_performance, most_spiked_performance, top_percent_performance = get_current_performance(np.zeros(int(num_examples / update_interval)), np.zeros(int(num_examples / update_interval)), np.zeros(int(num_examples / update_interval)), 0)

		# set firing rates to zero initially
		for name in input_population_names:
			input_groups[name + 'e'].rate = 0

		# initialize network
		j = 0
		num_retries = 0
		b.run(0)

		# start recording time
		start_time = timeit.default_timer()

		while j < num_examples:
			# getting firing rates based on training or test phase
			if test_mode:
				rates = (this.data['x'][j % 10000, :, :] / 8.0) * input_intensity
			else:
				# ensure weights don't grow without bound
				normalize_weights()
				# get the firing rates of the next input example
				rates = (this.data['x'][j % 60000, :, :] / 8.0) * input_intensity
			
			# plot the input at this step
			if do_plot:
				input_image_monitor = update_input(rates, input_image_monitor, input_image)
			
			# sets the input firing rates
			input_groups['Xe'].rate = rates.reshape(n_input)
			
			# run the network for a single example time
			b.run(single_example_time)
			
			# get new neuron label assignments every 'update_interval'
			if j % update_interval == 0 and j > 0:
				assignments = get_new_assignments(result_monitor[:], input_numbers[j - update_interval : j])
			
			# get count of spikes over the past iteration
			current_spike_count = np.copy(spike_counters['Ae'].count[:]).reshape((conv_features, n_e)) - previous_spike_count
			previous_spike_count = np.copy(spike_counters['Ae'].count[:]).reshape((conv_features, n_e))

			# set weights to those of the most-fired neuron
			if not test_mode and weight_sharing == 'weight_sharing':
				set_weights_most_fired()

			# update weights every 'weight_update_interval'
			if j % weight_update_interval == 0 and not test_mode and do_plot:
				update_2d_input_weights(input_weight_monitor, fig_weights)
				update_patch_weights(patch_weight_monitor, fig2_weights)
				# update_neuron_votes(neuron_vote_monitor, fig_neuron_votes)

			# if the neurons in the network didn't spike more than four times
			if np.sum(current_spike_count) < 5 and num_retries < 3:
				# increase the intensity of input
				input_intensity += 2
				num_retries += 1
				
				# set all network firing rates to zero
				for name in input_population_names:
					input_groups[name + 'e'].rate = 0

				# let the network relax back to equilibrium
				b.run(resting_time)
			# otherwise, record results and continue simulation
			else:
				num_retries = 0
				# record the current number of spikes
				result_monitor[j % update_interval, :] = current_spike_count
				
				# decide whether to evaluate on test or training set
				if test_mode:
					input_numbers[j] = this.data['y'][j % 10000][0]
				else:
					input_numbers[j] = this.data['y'][j % 60000][0]
				
				# get the output classifications of the network
				all_output_numbers[j, :], most_spiked_output_numbers[j, :], top_percent_output_numbers[j, :] = get_recognized_number_ranking(assignments, result_monitor[j % update_interval, :])
				
				# print progress
				if j % print_progress_interval == 0 and j > 0:
					print 'runs done:', j, 'of', int(num_examples), '(time taken for past', print_progress_interval, 'runs:', str(timeit.default_timer() - start_time) + ')'
					start_time = timeit.default_timer()
				
				# plot performance if appropriate
				if j % update_interval == 0 and j > 0:
					if do_plot_performance and do_plot:
						# updating the performance plot
						perf_plot, all_performance, most_spiked_performance, top_percent_performance = update_performance_plot(performance_monitor, all_performance, most_spiked_performance, top_percent_performance, j, fig_performance)
					else:
						all_performance, most_spiked_performance, top_percent_performance = get_current_performance(all_performance, most_spiked_performance, top_percent_performance, j)

					# printing out classification performance results so far
					print '\nClassification performance (all vote): ', all_performance[:int(j / float(update_interval)) + 1], '\n', 'Average performance:', sum(all_performance[:int(j / float(update_interval)) + 1]) / float(len(all_performance[:int(j / float(update_interval)) + 1])), '\n'
					print '\nClassification performance (most-spiked vote): ', most_spiked_performance[:int(j / float(update_interval)) + 1], '\n', 'Average performance:', sum(most_spiked_performance[:int(j / float(update_interval)) + 1]) / float(len(most_spiked_performance[:int(j / float(update_interval)) + 1])), '\n'
					print '\nClassification performance (top', str(top_percent), 'percentile vote): ', top_percent_performance[:int(j / float(update_interval)) + 1], '\n', 'Average performance:', sum(top_percent_performance[:int(j / float(update_interval)) + 1]) / float(len(top_percent_performance[:int(j / float(update_interval)) + 1])), '\n'			

					target = open('../performance/conv_patch_connectivity_performance/' + ending + '.txt', 'w')
					target.truncate()
					target.write('Iteration ' + str(j) + '\n')
					target.write(str(all_performance[:int(j / float(update_interval)) + 1]))
					target.write('\n')
					target.write(str(sum(all_performance[:int(j / float(update_interval)) + 1]) / float(len(all_performance[:int(j / float(update_interval)) + 1]))))
					target.write('\n')
					target.write(str(most_spiked_performance[:int(j / float(update_interval)) + 1]))
					target.write('\n')
					target.write(str(sum(most_spiked_performance[:int(j / float(update_interval)) + 1]) / float(len(most_spiked_performance[:int(j / float(update_interval)) + 1]))))
					target.write('\n')
					target.write(str(top_percent_performance[:int(j / float(update_interval)) + 1]))
					target.write('\n')
					target.write(str(sum(top_percent_performance[:int(j / float(update_interval)) + 1]) / float(len(top_percent_performance[:int(j / float(update_interval)) + 1]))))
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
		if not test_mode and weight_sharing == 'weight_sharing':
			set_weights_most_fired()


def save_and_plot_results():
	global fig_num
	
	print '...saving results'

	if not test_mode:
		save_theta()
	if not test_mode:
		save_connections()
	else:
		np.save(top_level_path + 'activity/conv_patch_connectivity_activity/results_' + str(num_examples) + '_' + ending, result_monitor)
		np.save(top_level_path + 'activity/conv_patch_connectivity_activity/input_numbers_' + str(num_examples) + '_' + ending, input_numbers)

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
		plot_patch_weights()

		b.ioff()
		b.show()


if __name__ == '__main__':
	parser = argparse.ArgumentParser()

	parser.add_argument('-m',  '--mode', default='train')
	parser.add_argument('-c',  '--connectivity', default='all')
	parser.add_argument('-wd', '--weight_dependence', default='no_weight_dependence')
	parser.add_argument('-pp', '--post_pre', default='post_pre')
	parser.add_argument('--conv_size', type=int, default=20)
	parser.add_argument('--conv_stride', type=int, default=2)
	parser.add_argument('--conv_features', type=int, default=25)
	parser.add_argument('--weight_sharing', default='no_weight_sharing')
	parser.add_argument('--lattice_structure', default='4')
	parser.add_argument('--random_inhibition_prob', type=float, default=0.0)
	parser.add_argument('--top_percent', type=int, default=10)
	
	args = parser.parse_args()
	mode, connectivity, weight_dependence, post_pre, conv_size, conv_stride, conv_features, weight_sharing, lattice_structure, random_inhibition_prob, top_percent = \
		args.mode, args.connectivity, args.weight_dependence, args.post_pre, args.conv_size, args.conv_stride, args.conv_features, args.weight_sharing, \
		args.lattice_structure, args.random_inhibition_prob, args.top_percent

	print '\n'

	print args.mode, args.connectivity, args.weight_dependence, args.post_pre, args.conv_size, args.conv_stride, args.conv_features, args.weight_sharing, \
		args.lattice_structure, args.random_inhibition_prob, args.top_percent

	print '\n'

	# set global preferences
	b.set_global_preferences(defaultclock = b.Clock(dt=0.5*b.ms), useweave = True, gcc_options = ['-ffast-math -march=native'], usecodegen = True,
		usecodegenweave = True, usecodegenstateupdate = True, usecodegenthreshold = False, usenewpropagate = True, usecstdp = True, openmp = False,
		magic_useframes = False, useweave_linear_diffeq = True)

	# for reproducibility's sake
	np.random.seed(0)

	# STDP rule
	stdp_input = weight_dependence + '_' + post_pre
	if weight_dependence == 'weight_dependence':
		use_weight_dependence = True
	else:
		use_weight_dependence = False
	if post_pre == 'post_pre':
		use_post_pre = True
	else:
		use_post_pre = False

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
		if use_post_pre:
			eqs_stdp_pre_ee = 'pre = 1.; w -= nu_ee_pre * post'
			eqs_stdp_post_ee = 'w += nu_ee_post * pre; post = 1.'

		else:
			eqs_stdp_pre_ee = 'pre = 1.'
			eqs_stdp_post_ee = 'w += nu_ee_post * pre; post = 1.'

	print '\n'

	# set ending of filename saves
	ending = connectivity + '_' + str(conv_size) + '_' + str(conv_stride) + '_' + str(conv_features) + '_' + str(n_e) + '_' + weight_dependence + '_' + post_pre + '_' + weight_sharing + '_' + lattice_structure + '_' + str(random_inhibition_prob)

	b.ion()
	fig_num = 1

	# creating convolution locations inside the input image
	convolution_locations = {}
	for n in xrange(n_e):
		convolution_locations[n] = [ ((n % n_e_sqrt) * conv_stride + (n // n_e_sqrt) * n_input_sqrt * conv_stride) + (x * n_input_sqrt) + y for y in xrange(conv_size) for x in xrange(conv_size) ]
	
	# instantiating neuron "vote" monitor
	result_monitor = np.zeros((update_interval, conv_features, n_e))

	# build the spiking neural network
	network = ConvSpikingNN()
	network.build_connectivity()

	# bookkeeping variables
	previous_spike_count = np.zeros((conv_features, n_e))
	assignments = np.zeros((conv_features, n_e))
	input_numbers = [0] * num_examples
	all_output_numbers = np.zeros((num_examples, 10))
	most_spiked_output_numbers = np.zeros((num_examples, 10))
	top_percent_output_numbers = np.zeros((num_examples, 10))
	rates = np.zeros((n_input_sqrt, n_input_sqrt))

	# run the simulation of the network
	run_simulation()

	# save and plot results
	save_and_plot_results()