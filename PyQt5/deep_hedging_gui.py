# Commented out IPython magic to ensure Python compatibility.
#@title <font color='Blue'>**Overheads**</font>

# Author: Yu-Man Tam
# Last updated: 4/5/2020

# Reference: Deep Hedging (2019, Quantitative Finance) by Buehler et al.
# https://www.tandfonline.com/doi/abs/10.1080/14697688.2019.1571683

# Qt references: https://doc.qt.io/qt-5/qmainwindow.html

import sys, os
sys.path.insert(0, os.getcwd() + "/lib/qt")
sys.path.insert(0, os.getcwd() + "/lib")

import time

# Linear algebra, finance, and machine learning libraries
import numpy as np
import QuantLib as ql
import tensorflow as tf

from tensorflow.keras.optimizers import Adam
from tensorflow.keras.models import Model
from tensorflow.keras.layers import Input, Concatenate
from scipy.stats import norm

# For PyQtgraph
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore, QtGui
from pyqtgraph.parametertree import ParameterTree, Parameter
from pyqtgraph.widgets.RemoteGraphicsView import RemoteGraphicsView

# User-defined libraries
from stochastic_processes import BlackScholesProcess
from instruments import European_Call
from deep_hedging import Deep_Hedging_Model
from loss_metrics import Entropy, CVaR
from utilities import train_test_split
from default_params import Deep_Hedging_Params

# Tensorflow settings
tf.compat.v1.logging.set_verbosity(tf.compat.v1.logging.ERROR)

# Default Parameters
# European call option (short).
payoff_func = lambda x: -np.maximum(x - strike, 0.0)
calculation_date = ql.Date.todaysDate()

# Day convention.
day_count = ql.Actual365Fixed() # Actual/Actual (ISDA)         

# Information set (in string)
# Choose from: S, log_S, normalized_log_S (by S0)
information_set = "normalized_log_S"

# Loss function
# loss_type = "CVaR" (Expected Shortfall) -> loss_param = alpha 
# loss_type = "Entropy" -> loss_param = lambda
loss_type = "Entropy"

# Other NN parameters
use_batch_norm = False
kernel_initializer = "he_uniform"

activation_dense = "leaky_relu"
activation_output = "sigmoid"
final_period_cost = False

# Number of bins to plot for the PnL histograms.
num_bins = 30

app = QtGui.QApplication([])

# Need a separate threads for deep hedging algo and plot the graphs.
class DH_Worker(QtCore.QThread):
	DH_outputs = QtCore.pyqtSignal(np.ndarray, np.ndarray, np.ndarray, np.float32, float, float)
	def __init__(self):
		QtCore.QThread.__init__(self)
		self._exit = False
		self._pause = False
		
	def __del__(self):
		self.wait()
		
	def run_deep_hedge_algo(self, training_dataset = None, epochs = None, Ktrain = None, batch_size = None, \
								model = None, submodel = None, loss_param = None, learning_rate = None, xtest = None, \
								initial_price_BS = None, width = None, I_range = None, x_range = None):
		self.training_dataset = training_dataset
		self.Ktrain = Ktrain
		self.batch_size = batch_size
		self.model = model
		self.submodel = submodel
		self.loss_param = loss_param
		self.initial_price_BS = initial_price_BS
		self.width = width
		self.epochs = epochs
		self.xtest = xtest
		self.I_range = I_range
		self.x_range = x_range
		self.learning_rate = learning_rate
		self.start()
		
	def pause(self):
		self._pause = True
		
	def cont(self):
		self._pause = False
		
	def stop(self):
		self._exit = True
		self.exit()
		
	def is_running(self):
		if self._pause or self._exit:
			return False
		else:
			return True
		
	def run(self):
		certainty_equiv = tf.Variable(0.0, name = "certainty_equiv")
		
		# Accelerator Function.
		model_func = tf.function(self.model)
		submodel_func = tf.function(self.submodel)
		
		optimizer = Adam(learning_rate=self.learning_rate)
		
		num_epoch = 0
		start = time.time()
		while num_epoch <= self.epochs:
			# Exit event loop if the exit flag is set to True.
			if self._exit:
				mini_batch_iter = None
				self._exit = False
				self._pause = False
				break

			if not self._pause:
				try:
					mini_batch = mini_batch_iter.next()
				except:
					num_batch = 0
					num_epoch += 1 
					
					mini_batch_iter = self.training_dataset.shuffle(self.Ktrain).batch(self.batch_size).__iter__()
					mini_batch = mini_batch_iter.next()
			
				num_batch += 1
				
				# Record gradient
				with tf.GradientTape() as tape:
					wealth = model_func(mini_batch)
					loss = Entropy(wealth, certainty_equiv, self.loss_param)

				PnL_DH = model_func(self.xtest).numpy().squeeze() # Out-of-sample
				DH_delta = submodel_func(np.expand_dims(self.I_range,axis=1)).numpy().squeeze()
				DH_bins, _ = np.histogram(PnL_DH+self.initial_price_BS, bins = num_bins, range = self.x_range)
				
				# Forward and backward passes
				grads = tape.gradient(loss, self.model.trainable_weights)
				optimizer.apply_gradients(zip(grads, self.model.trainable_weights))
				
				end = time.time()
				if end - start > 0:
					self.DH_outputs.emit(PnL_DH, DH_delta, DH_bins, loss.numpy().squeeze(), \
																	num_epoch, num_batch)
					start = end
			else:
				time.sleep(1)

class MainWindow(QtWidgets.QMainWindow):
	def __init__(self):
		# Inheritance from the QMainWindow class
		# Reference: https://doc.qt.io/qt-5/qmainwindow.html
		super().__init__()
		self.days_from_today = 15
		self.Thread_RunDH = DH_Worker()
		
		# The order of code is important here: Make sure the emitted signals are connected
		# before actually running the Worker.
		self.Thread_RunDH.DH_outputs["PyQt_PyObject", "PyQt_PyObject", "PyQt_PyObject", \
				"PyQt_PyObject", "double", "double"].connect(self.Update_PnL_Hist_Widget)
		self.Thread_RunDH.DH_outputs["PyQt_PyObject", "PyQt_PyObject", "PyQt_PyObject", \
				"PyQt_PyObject", "double", "double"].connect(self.Update_Delta_Plot_Widget)		
			
		# Define a top-level widget to hold everything
		self.w = QtGui.QWidget()
		
		# Create a grid layout to manage the widgets size and position
		self.layout = QtGui.QGridLayout()
		self.w.setLayout(self.layout)
		
		self.setCentralWidget(self.w)

		# Add the parameter menu.
		self.tree = self.Deep_Hedging_Parameter_Widget()
		self.layout.addWidget(self.tree, 0, 0, 3, 2)   # upper-left
	
		self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
		self.tree.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
		self.tree.setMinimumSize(350,525)
		
		# Add a run button
		self.run_btn = QtGui.QPushButton('Run')
		self.layout.addWidget(self.run_btn, 3, 0, 1, 1)   # button goes in upper-left
		
		# Add a pause button
		self.pause_btn = QtGui.QPushButton('Pause')
		self.layout.addWidget(self.pause_btn, 3, 1, 1, 1)   # button goes in upper-left
		
		# Run the deep hedging algo in a separate thread when the run button is clicked.
		self.run_btn.clicked.connect(self.RunButton)
		
		# Pause button.
		self.pause_btn.clicked.connect(self.Pause)
		
	def Deep_Hedging_Parameter_Widget(self):
		tree = ParameterTree()
		
		## Create tree of Parameter objects
		self.params = Parameter.create(name='params', type='group', children=Deep_Hedging_Params())
		tree.setParameters(self.params, showTop=False)
	
		return tree
	
	# Define the event when the "Run" button is clicked.
	def RunButton(self):
		if self.run_btn.text() == "Stop":
			self.Thread_RunDH.stop()
			if self.pause_btn.text() == "Continue":
				self.pause_btn.setText("Pause")
			self.run_btn.setText("Run")
		elif self.run_btn.text() == "Run":
			self.run_btn.setText("Stop")
			
			# Set parameters
			self.Ktrain = self.params.param("Monte-Carlo Simulation", 'Sample Size', "Training").value()
			self.Ktest_ratio = self.params.param("Monte-Carlo Simulation", 'Sample Size', "Testing (as fraction of Training)").value()
			self.N = self.params.param("European Call", "Maturity (in days)").value()
			self.S0 = self.params.param("European Call", "S0").value()
			self.strike = self.params.param("European Call", "Strike").value()
			self.sigma = self.params.param("European Call", "Implied Volatility").value()
			self.risk_free = self.params.param("European Call", "Risk-Free Rate").value()
			self.dividend = self.params.param("European Call", "Dividend Yield").value()
			
			self.loss_param = self.params.param("Deep Hedging Strategy", 'Loss Function (Exponential)', "Risk Aversion").value()
			self.epsilon = self.params.param("European Call", "Proportional Transaction Cost", "Cost").value()
			self.d = self.params.param("Deep Hedging Strategy", "Network Structure", "Number of Hidden Layers").value()
			self.m = self.params.param("Deep Hedging Strategy", "Network Structure", "Number of Neurons").value()
			self.strategy_type = self.params.param("Deep Hedging Strategy", "Network Structure", "Network Type").value()
			self.lr = self.params.param("Deep Hedging Strategy", "Learning Parameters", "Learning Rate").value()
			self.batch_size = self.params.param("Deep Hedging Strategy", "Learning Parameters", "Mini-Batch Size").value()
			self.epochs = self.params.param("Deep Hedging Strategy", "Learning Parameters", "Number of Epochs").value()
			
			self.maturity_date = calculation_date + self.N
			self.payoff_func = lambda x: -np.maximum(x - self.strike, 0.0)
			
			# Simulate the stock price process.
			self.S = self.simulate_stock_prices()
			
			# Assemble the dataset for training and testing.
			# Structure of data:
			#   1) Trade set: [S]
			#   2) Information set: [S] 
			#   3) payoff (dim = 1)
			self.training_dataset = self.assemble_data()

			# Compute Black-Scholes prices for benchmarking.
			self.price_BS, self.delta_BS, self.PnL_BS = self.get_Black_Scholes_Prices()
			
			# Define model and sub-models
			self.model = self.Define_DH_model()
			self.submodel = self.Define_DH_Delta_Strategy_Model()
			
			# Add the PnL histogram (PlotWidget) - Black-Scholes vs Deep Hedging.
			self.fig_PnL_view, self.fig_PnL = self.PnL_Hist_Widget()
			self.layout.addWidget(self.fig_PnL_view, 0, 3, 2, 1) 
			self.fig_PnL_view.setMinimumWidth(600)
			
			# Add the Delta line plot (PlotWidget) - Black-Scholes vs Deep Hedging.
			self.fig_delta_view, self.fig_delta = self.Delta_Plot_Widget()
			self.layout.addWidget(self.fig_delta_view, 0, 4, 2, 1)
			self.fig_delta_view.setMinimumWidth(600)
			
			# Add the loss plot (PlotWidget) - Black-Scholes vs Deep Hedging.
			self.fig_loss = self.Loss_Plot_Widget()
			self.layout.addWidget(self.fig_loss, 2, 3, 2, 2)
			self.fig_loss.setMinimumWidth(1200)
			
			# Run the deep hedging algo in a separate thread.
			self.Thread_RunDH.run_deep_hedge_algo(training_dataset = self.training_dataset, epochs = self.epochs, \
									Ktrain = self.Ktrain, batch_size = self.batch_size, model = self.model, \
									submodel = self.submodel, loss_param = self.loss_param, learning_rate = self.lr, xtest = self.xtest, \
									initial_price_BS = self.price_BS[0][0], width = self.width, I_range = self.I_range, x_range = self.x_range)

	# Define action when the Pause button is clicked.
	def Pause(self):
		if self.pause_btn.text() == "Pause":
			self.Thread_RunDH.pause()
			self.pause_btn.setText("Continue")
		elif self.pause_btn.text() == "Continue":
			self.Thread_RunDH.cont()
			self.pause_btn.setText("Pause")
	
	# Define deep hedging model
	def Define_DH_model(self):
		# Setup and compile the model
		model = Deep_Hedging_Model(N=self.N, d=self.d+2, m=self.m, risk_free=self.risk_free, \
													dt = self.dt, strategy_type=self.strategy_type, epsilon = self.epsilon, \
													use_batch_norm = use_batch_norm, kernel_initializer = kernel_initializer, \
													activation_dense = activation_dense, activation_output = activation_output, \
													final_period_cost = final_period_cost)
		# Accelerate the code using tf.function. 
		model_func = tf.function(model)
		
		return model
	
	def Define_DH_Delta_Strategy_Model(self):
		if self.strategy_type == "simple":
			# Set up the sub-model that outputs the delta.
			submodel = Model(self.model.get_layer("dense_0_" + \
								str(self.days_from_today)).input, self.model.get_layer("delta_" + \
								str(self.days_from_today)).output)
		elif self.strategy_type == "recurrent":
			# For "recurrent", the information set is price as well as the past delta.
			inputs = [Input(1,), Input(1,)]
			
			outputs = Concatenate()(inputs)

			flag_add_layer = False
			
			num_layers = len(self.model.layers)
			for idx in range(num_layers):
				if self.model.layers[idx].name == "dense_0_" + str(self.days_from_today):
					flag_add_layer = True
				elif self.model.layers[idx].name == "delta_15":
					outputs = self.model.layers[idx](outputs)
					break
				
				if flag_add_layer_for_submodel:
					outputs = self.model.layers[idx](outputs)
					
			submodel = Model(inputs=inputs, outputs=outputs)
	
		return submodel
		
	# Draw PnL histogram (PlotWidget) - Black-Scholes vs Deep Hedging.
	def PnL_Hist_Widget(self):		
		# Initialize the PnL Histogram Widget.
		# See issue: https://github.com/pyqtgraph/pyqtgraph/issues/1153
		
		# Speed up options see here:
		# http://www.pyqtgraph.org/downloads/0.10.0/pyqtgraph-0.10.0-deb/pyqtgraph-0.10.0/examples/RemoteSpeedTest.py
		
		# Setup remote process.
		fig_PnL_view = RemoteGraphicsView()
		fig_PnL_view.pg.setConfigOptions(antialias=True)
		
		fig_PnL = fig_PnL_view.pg.PlotItem()
		fig_PnL_view.setCentralItem(fig_PnL)

		self.x_range = (self.PnL_BS.min()+self.price_BS[0,0], self.PnL_BS.max()+self.price_BS[0,0])
		self.BS_bins, self.bin_edges = np.histogram(self.PnL_BS+self.price_BS[0,0], bins = num_bins, range = self.x_range)
		self.width = (self.bin_edges[1] - self.bin_edges[0])/2.0

		# PnL histogram for Black-Scholes.
		self.BS_hist = fig_PnL_view.pg.BarGraphItem(x=self.bin_edges[:-2], height=self.BS_bins, width=self.width, brush='r')
		fig_PnL.addItem(self.BS_hist)
		
		# PnL histogram for Black-Scholes.
		self.DH_hist = fig_PnL_view.pg.BarGraphItem(x=self.bin_edges[:-2]+self.width, height=self.BS_bins*0.0, width=self.width, brush='b')
		fig_PnL.addItem(self.DH_hist)
		
		return fig_PnL_view, fig_PnL
	
	# Update PnL histogram (PlotWidget) - Black-Scholes vs Deep Hedging.
	def Update_PnL_Hist_Widget(self, PnL_DH = None, DH_delta = None, DH_bins = None, \
															loss = None, num_epoch = None, num_batch = None):
		self.DH_hist.setOpts(height=DH_bins)
		self.fig_PnL.setTitle(str(num_epoch) + "," + str(num_batch))	
		
	# Draw Delta plot (PlotWidget) - Black-Scholes vs Deep Hedging.
	# Assume the PnL_Hist_Widget ran first, so we don't need to run the model again.
	def Delta_Plot_Widget(self):
		self.tau = self.days_from_today*self.dt
		
		self.min_S = self.S_test[0][:,self.days_from_today].min()
		self.max_S = self.S_test[0][:,self.days_from_today].max()
		self.S_range = np.linspace(self.min_S,self.max_S,101)

		# Attention: Need to transform it to be consistent with the information set.
		if information_set is "S":
			self.I_range =  self.S_range # Information set
		elif information_set is "log_S":
			self.I_range =  np.log(self.S_range)
		elif information_set is "normalized_log_S":
			self.I_range =  np.log(self.S_range/self.S0)		
			
		# Compute Black-Scholes delta for S_range.
		# Reference: https://en.wikipedia.org/wiki/Greeks_(finance)
		self.d1 = (np.log(self.S_range) - np.log(self.strike) + (self.risk_free - self.dividend + (self.sigma**2)/2)*self.tau) \
					/ (self.sigma*np.sqrt(self.tau))  
					
		self.model_delta = norm.cdf(self.d1)*np.exp(-self.dividend*self.tau)
		
		# Setup remote process.
		fig_delta_view = RemoteGraphicsView()
		fig_delta_view.pg.setConfigOptions(antialias=True)
		
		fig_delta = fig_delta_view.pg.PlotItem()
		fig_delta_view.setCentralItem(fig_delta)
		
		# Delta plot for Black-Scholes
		self.BS_delta_plot = fig_delta_view.pg.PlotDataItem()
		self.BS_delta_plot.setPen(color ="r", width=2.5)
		self.BS_delta_plot.setDownsampling(auto=True, method="peak")
		self.BS_delta_plot.setData(self.S_range, self.model_delta)
		
		fig_delta.addItem(self.BS_delta_plot)

		# Delta plot for Deep-Hedging
		self.DH_delta_plot = fig_delta_view.pg.ScatterPlotItem(brush='b', size=5)
		self.DH_delta_plot.setPen(brush='b', size=5)
		
		fig_delta.addItem(self.DH_delta_plot)		
		
		return fig_delta_view, fig_delta
		
	# Update Delta Plot (PlotWidget) - Black-Scholes vs Deep Hedging.
	def Update_Delta_Plot_Widget(self, PnL_DH = None, DH_delta = None, DH_bins = None, \
															loss = None, num_epoch = None, num_batch = None):
		self.DH_delta_plot.setData(self.S_range,DH_delta, symobol="o")
		self.fig_delta.setTitle(str(num_epoch) + "," + str(num_batch))

	# Draw loss plot (PlotWidget) - Black-Scholes vs Deep Hedging.
	def Loss_Plot_Widget(self):
		fig_loss = pg.PlotWidget()
		self.BS_loss_plot = pg.PlotCurveItem(pen = pg.mkPen(color="r", width=2.5))
		fig_loss.addItem(self.BS_loss_plot)
		# Work here.
		return fig_loss
		
	# Update loss plot (PlotWidget) - Black-Scholes vs Deep Hedging.
	def Update_Loss_Plot_Widget(self, PnL_DH = None, DH_delta = None, DH_bins = None, \
																loss = None, num_epoch = None, num_batch = None):
		if num_epoch == 1 and num_batch == 1:
			pass
			# self.DH_hist = pg.BarGraphItem(x=self.bin_edges[:-2]+self.width, height=DH_bins, width=self.width, brush='b')
			# self.fig_PnL.addItem(self.DH_hist)
		else:
			pass
			# self.DH_hist.setOpts(height=DH_bins)
		
	def simulate_stock_prices(self):
		self.nobs = int(self.Ktrain*(1+self.Ktest_ratio)) # Total obs = Training + Testing
		
		# Length of one time-step (as fraction of a year).
		self.dt = day_count.yearFraction(calculation_date,calculation_date + 1) 
		self.maturity = self.N*self.dt # Maturities (in the unit of a year)

		self.stochastic_process = BlackScholesProcess(s0 = self.S0, sigma = self.sigma, \
													risk_free = self.risk_free, dividend = self.dividend, day_count=day_count)
													
		print("\nRun Monte-Carlo Simulations for the Stock Price Process.\n")
		return self.stochastic_process.gen_path(self.maturity, self.N, self.nobs)
		
	def assemble_data(self):
		self.payoff_T = self.payoff_func(self.S[:,-1]) # Payoff of the call option

		self.trade_set =  np.stack((self.S),axis=1) # Trading set

		if information_set is "S":
			self.I =  np.stack((self.S),axis=1) # Information set
		elif information_set is "log_S":
			self.I =  np.stack((np.log(self.S)),axis=1)
		elif information_set is "normalized_log_S":
			self.I =  np.stack((np.log(self.S/self.S0)),axis=1)
			
		# Structure of xtrain:
		#   1) Trade set: [S]
		#   2) Information set: [S] 
		#   3) payoff (dim = 1)
		self.x_all = []
		for i in range(self.N+1):
			self.x_all += [self.trade_set[i,:,None]]
			if i != self.N:
				self.x_all += [self.I[i,:,None]]
		self.x_all += [self.payoff_T[:,None]]

		# Split the entire sample into a training sample and a testing sample.
		self.test_size = int(self.Ktrain*self.Ktest_ratio)
		[self.xtrain, self.xtest] = train_test_split(self.x_all, test_size=self.test_size)
		[self.S_train, self.S_test] = train_test_split([self.S], test_size=self.test_size)
		[self.option_payoff_train, self.option_payoff_test] = \
				train_test_split([self.x_all[-1]], test_size=self.test_size)

		# Convert the training sample into tf.Data format (same as xtrain).
		training_dataset = tf.data.Dataset.from_tensor_slices(tuple(self.xtrain))
		return training_dataset.cache()
		
	def get_Black_Scholes_Prices(self):
		# Obtain Black-Scholes price, delta, and PnL
		call = European_Call()
		price_BS = call.get_BS_price(S = self.S_test[0], sigma = self.sigma, risk_free = self.risk_free, \
											dividend = self.dividend, K = self.strike, exercise_date = self.maturity_date, \
											calculation_date = calculation_date, day_count = day_count, dt = self.dt)
		delta_BS = call.get_BS_delta(S = self.S_test[0], sigma = self.sigma, risk_free = self.risk_free, \
											dividend = self.dividend, K = self.strike, exercise_date = self.maturity_date, \
											calculation_date = calculation_date, day_count = day_count, dt = self.dt)
		PnL_BS =  call.get_BS_PnL(S= self.S_test[0], payoff=self.payoff_func(self.S_test[0][:,-1]), \
											delta=delta_BS, dt=self.dt, risk_free = self.risk_free, \
											final_period_cost=final_period_cost, epsilon=self.epsilon)
		return price_BS, delta_BS, PnL_BS
		
if __name__ == '__main__':
	main = MainWindow()
	main.show()
	
	if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
			QtGui.QApplication.instance().exec_()