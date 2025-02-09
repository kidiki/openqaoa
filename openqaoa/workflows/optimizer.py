#   Copyright 2022 Entropica Labs
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from abc import ABC
import numpy as np
import copy
from openqaoa.devices import DeviceLocal, DeviceBase

from openqaoa.problems.problem import QUBO
from openqaoa.problems.helper_functions import convert2serialize
from openqaoa.workflows.parameters.qaoa_parameters import CircuitProperties, BackendProperties, ClassicalOptimizer
from openqaoa.workflows.parameters.rqaoa_parameters import RqaoaParameters, ALLOWED_RQAOA_TYPES
from openqaoa.qaoa_parameters import Hamiltonian, QAOACircuitParams, create_qaoa_variational_params
from openqaoa.utilities import get_mixer_hamiltonian, ground_state_hamiltonian, exp_val_hamiltonian_termwise
from openqaoa.backends.qaoa_backend import get_qaoa_backend, DEVICE_NAME_TO_OBJECT_MAPPER, DEVICE_ACCESS_OBJECT_MAPPER
from openqaoa.optimizers.qaoa_optimizer import get_optimizer
from openqaoa.basebackend import QAOABaseBackendStatevector
from openqaoa import rqaoa
from openqaoa.rqaoa.rqaoa_results import RQAOAResults


class Optimizer(ABC):
    """
    Abstract class to represent an optimizer

    It's basic usage consists of 

     #. Initialization
     #. Compilation
     #. Optimization
    
    Attributes
    ----------
        device: `DeviceBase`
            Device to be used by the optimizer
        backend_properties: `BackendProperties`
            The backend properties of the optimizer workflow. Use to set the backend properties such as the number of shots and the cvar values.
            For a complete list of its parameters and usage please see the method set_backend_properties
        classical_optimizer: `ClassicalOptimizer`
            The classical optimiser properties of the optimizer workflow. Use to set the classical optimiser needed for the classical optimisation part of the optimizer routine.
            For a complete list of its parameters and usage please see the method set_classical_optimizer
        local_simulators: `list[str]`
            A list containing the available local simulators
        cloud_provider: `list[str]`
            A list containing the available cloud providers
        compiled: `Bool`
            A boolean flag to check whether the optimizer object has been correctly compiled at least once     
    """

    def __init__(self, device=DeviceLocal('vectorized')):  
        """
        Initialize the optimizer class.

        Parameters
        ----------
        device: `DeviceBase`
            Device to be used by the optimizer. Default is using the local 'vectorized' simulator.
        """
        
        self.device = device
        self.backend_properties = BackendProperties()
        self.classical_optimizer = ClassicalOptimizer()
        self.local_simulators = list(DEVICE_NAME_TO_OBJECT_MAPPER.keys())
        self.cloud_provider = list(DEVICE_ACCESS_OBJECT_MAPPER.keys())
        self.compiled = False

    def asdict(self):
        attributes_dict = convert2serialize(self)
        return attributes_dict

    def set_device(self, device: DeviceBase):
        """"
        Specify the device to be used by the QAOA.

        Parameters
        ----------
        location: `str`
            Can be either local, qcs, or ibmq
        name: `str`
            The name of the device to be used, for local simulators please refer to `q.local_simulators`.
            For cloud providers please refer to the provider's naming conventions
        """
        self.device = device

    def set_backend_properties(self, **kwargs):
        """
        Set the backend properties

        Parameters
        -------------------
            device: DeviceBase
            prepend_state: [Union[QuantumCircuitBase,List[complex], np.ndarray]
                The state prepended to the circuit.
            append_state: [Union[QuantumCircuitBase,List[complex], np.ndarray]
                The state prepended to the circuit.
            init_hadamard: bool
            Whether to apply a Hadamard gate to the beginning of the 
                QAOA part of the circuit.. Defaults to `True`
            n_shots: int
            Optional argument to specify the number of shots required to run QAOA computations
                on shot-based simulators and QPUs. Defaults to 100.
            cvar_alpha: float
                The value of alpha for the CVaR cost function
            noise_model: `qiskit.providers.aer.noise.NoiseModel`
                    The Qiskit noise model to be used for the simulation.
            qiskit_simulation_method: str, optional
                The method to be used for the simulation.
            seed_simulator: int
                Optional argument to initialize a pseudorandom solution. Default None
            active_reset:
                #TODO
            rewiring:
                Rewiring scheme to be used for Pyquil. 
                Either 'PRAGMA INITIAL_REWIRING "NAIVE"' or 
                'PRAGMA INITIAL_REWIRING "PARTIAL"'. If None, defaults to NAIVE
            disable_qubit_rewiring: `bool`
                Disable automatic qubit rewiring on AWS braket backend
        """

        for key, value in kwargs.items():
            if hasattr(self.backend_properties, key):
                pass# setattr(self.backend_properties, key, value)
            else:
                raise ValueError(
                    f'Specified argument `{value}` for `{key}` in set_backend_properties is not supported')

        self.backend_properties = BackendProperties(**kwargs)
        return None

    def set_classical_optimizer(self, **kwargs):
        """
        Set the parameters for the classical optimizer to be used in the optimizers workflow

        Parameters
        ----------
            method: str
                The classical optimization method. Choose from:
                 ['imfil','bobyqa','snobfit']
                 ['vgd', 'sgd', 'rmsprop'] 
                 ['nelder-mead','powell','cg','bfgs','newton-cg','l-bfgs-b','cobyla'] 
            maxiter : Optional[int]
                Maximum number of iterations.
            maxfev : Optional[int]
                Maximum number of function evaluations.
            jac: str
                Method to compute the gradient vector. Choose from:
                    - ['finite_difference', 'param_shift', 'stoch_param_shift', 'grad_spsa']        
            hess: str
                Method to compute the hessian. Choose from:
                    - ['finite_difference', 'param_shift', 'stoch_param_shift', 'grad_spsa']
            constraints: scipy.optimize.LinearConstraints, scipy.optimize.NonlinearConstraints  
                Scipy-based constraints on parameters of optimization. Will be available soon
            bounds: scipy.optimize.Bounds
                Scipy-based bounds on parameters of optimization. Will be available soon
            tol : float
                Tolerance before the optimizer terminates; if `tol` is larger than
                the difference between two steps, terminate optimization.
            optimizer_options : dict
                Dictionary of optimiser-specific arguments.
                    stepsize : float
                        Step size of each gradient descent step.
                    decay : float
                        Stepsize decay parameter of RMSProp.
                    eps : float
                        Small number to prevent division by zero for RMSProp.
                    lambd : float
                        Small number to prevent singularity of QFIM matrix for Natural Gradient Descent.
            ramp_time: float
                The slope(rate) of linear ramp initialisation of QAOA parameters.
            jac_options : dict
                Dictionary that specifies gradient-computation options according to method chosen in 'jac'.
            hess_options : dict
                Dictionary that specifies Hessian-computation options according to method chosen in 'hess'.
            save_intermediate: bool
                If True, the intermediate parameters of the optimization and job ids, if available, are saved throughout the run. This is set to False by default.
        """
        for key, value in kwargs.items():
            if hasattr(self.classical_optimizer, key):
                pass #setattr(self.classical_optimizer, key, value)
            else:
                raise ValueError(
                    'Specified argument is not supported by the Classical Optimizer')

        self.classical_optimizer = ClassicalOptimizer(**kwargs)
        return None

    def compile():
        raise NotImplementedError

    def optimize():
        raise NotImplementedError


class QAOA(Optimizer):
    """
    A class implementing a QAOA workflow end to end.

    It's basic usage consists of 
    1. Initialization
    2. Compilation
    3. Optimization

    .. note::
        The attributes of the QAOA class should be initialized using the set methods of QAOA. For example, to set the circuit's depth to 10 you should run `set_circuit_properties(p=10)`

    Attributes
    ----------
        device: `DeviceBase`
            Device to be used by the optimizer
        circuit_properties: `CircuitProperties`
            The circuit properties of the QAOA workflow. Use to set depth `p`, choice of parametrisation, parameter initialisation strategies, mixer hamiltonians.
            For a complete list of its parameters and usage please see the method set_circuit_properties
        backend_properties: `BackendProperties`
            The backend properties of the QAOA workflow. Use to set the backend properties such as the number of shots and the cvar values.
            For a complete list of its parameters and usage please see the method set_backend_properties
        classical_optimizer: `ClassicalOptimizer`
            The classical optimiser properties of the QAOA workflow. Use to set the classical optimiser needed for the classical optimisation part of the QAOA routine.
            For a complete list of its parameters and usage please see the method set_classical_optimizer
        local_simulators: `list[str]`
            A list containing the available local simulators
        cloud_provider: `list[str]`
            A list containing the available cloud providers
        mixer_hamil: Hamiltonian
            The desired mixer hamiltonian
        cost_hamil: Hamiltonian
            The desired mixer hamiltonian
        circuit_params: QAOACircuitParams
            the abstract and backend-agnostic representation of the underlying QAOA parameters
        variate_params: QAOAVariationalBaseParams
            The variational parameters. These are the parameters to be optimised by the classical optimiser
        backend: VQABaseBackend
            The openQAOA representation of the backend to be used to execute the quantum circuit
        optimizer: OptimizeVQA
            The classical optimiser
        results: `Result`
            Contains the logs of the optimisation process
        compiled: `Bool`
            A boolean flag to check whether the QAOA object has been correctly compiled at least once

    Examples
    --------
    Examples should be written in doctest format, and should illustrate how
    to use the function.

    >>> q = QAOA()
    >>> q.compile(QUBO)
    >>> q.optimise()

    Where `QUBO` is a an instance of `openqaoa.problems.problem.QUBO`

    If you want to use non-default parameters:

    >>> q_custom = QAOA()
    >>> q_custom.set_circuit_properties(p=10, param_type='extended', init_type='ramp', mixer_hamiltonian='x')
    >>> q_custom.set_device_properties(device_location='qcs', device_name='Aspen-11', cloud_credentials={'name' : "Aspen11", 'as_qvm':True, 'execution_timeout' : 10, 'compiler_timeout':10})
    >>> q_custom.set_backend_properties(n_shots=200, cvar_alpha=1)
    >>> q_custom.set_classical_optimizer(method='nelder-mead', maxiter=2)
    >>> q_custom.compile(qubo_problem)
    >>> q_custom.optimize()
    """

    def __init__(self, device=DeviceLocal('vectorized')):
        """
        Initialize the QAOA class.

        Parameters
        ----------
            device: `DeviceBase`
                Device to be used by the optimizer. Default is using the local 'vectorized' simulator.
        """
        super().__init__(device)
        self.circuit_properties = CircuitProperties()

    def set_circuit_properties(self, **kwargs):
        """
        Specify the circuit properties to construct QAOA circuit

        Parameters
        -------------------
            qubit_register: `list`
                Select the desired qubits to run the QAOA program. Meant to be used as a qubit
                selector for qubits on a QPU. Defaults to a list from 0 to n-1 (n = number of qubits)
            p: `int`
                Depth `p` of the QAOA circuit
            q: `int`
                Analogue of `p` of the QAOA circuit in the Fourier parameterisation
            param_type: `str`
                Choose the QAOA circuit parameterisation. Currently supported parameterisations include:
                `'standard'`: Standard QAOA parameterisation
                `'standard_w_bias'`: Standard QAOA parameterisation with a separate parameter for single-qubit terms.
                `'extended'`: Individual parameter for each qubit and each term in the Hamiltonian.
                `'fourier'`: Fourier circuit parameterisation
                `'fourier_extended'`: Fourier circuit parameterisation with individual parameter for each qubit and term in Hamiltonian.
                `'fourier_w_bias'`: Fourier circuit parameterisation with aseparate parameter for single-qubit terms
            init_type: `str`
                Initialisation strategy for the QAOA circuit parameters. Allowed init_types:
                `'rand'`: Randomly initialise circuit parameters
                `'ramp'`: Linear ramp from Hamiltonian initialisation of circuit parameters (inspired from Quantum Annealing)
                `'custom'`: User specified initial circuit parameters
            mixer_hamiltonian: `str`
                Parameterisation of the mixer hamiltonian:
                `'x'`: Randomly initialise circuit parameters
                `'xy'`: Linear ramp from Hamiltonian initialisation of circuit 
            mixer_qubit_connectivity: `[Union[List[list],List[tuple], str]]`
                The connectivity of the qubits in the mixer Hamiltonian. Use only if `mixer_hamiltonian = xy`. The user can specify the 
                connectivity as a list of lists, a list of tuples, or a string chosen from ['full', 'chain', 'star'].
            mixer_coeffs: `list`
                The coefficients of the mixer Hamiltonian. By default all set to -1
            annealing_time: `float`
                Total time to run the QAOA program in the Annealing parameterisation (digitised annealing)
            linear_ramp_time: `float`
                The slope(rate) of linear ramp initialisation of QAOA parameters.
            variational_params_dict: `dict`
                Dictionary object specifying the initial value of each circuit parameter for the chosen parameterisation, if the `init_type` is selected as `'custom'`.    
                For example, for standard parametrisation set {'betas': [0.1, 0.2, 0.3], 'gammas': [0.1, 0.2, 0.3]}
        """

        for key, value in kwargs.items():
            if hasattr(self.circuit_properties, key):
                pass
            else:
                raise ValueError(
                    "Specified argument is not supported by the circuit")
        self.circuit_properties = CircuitProperties(**kwargs)

        return None

    def compile(self, problem: QUBO = None, verbose: bool = False):
        """
        Initialise the trainable parameters for QAOA according to the specified
        strategies and by passing the problem statement

        .. note::
            Compilation is necessary because it is the moment where the problem statement and the QAOA instructions are used to build the actual QAOA circuit.

        .. tip::
            Set Verbose to false if you are running batch computations! 

        Parameters
        ----------
        problem: `Problem`
            QUBO problem to be solved by QAOA
        verbose: bool
            Set True to have a summary of QAOA to displayed after compilation
        """

        assert isinstance(problem, QUBO), "The problem must be converted into QUBO form"
        
        self.cost_hamil = Hamiltonian.classical_hamiltonian(
            terms=problem.terms, coeffs=problem.weights, constant=problem.constant)
        
        self.mixer_hamil = get_mixer_hamiltonian(n_qubits=self.cost_hamil.n_qubits,
                                                 mixer_type=self.circuit_properties.mixer_hamiltonian,
                                                 qubit_connectivity=self.circuit_properties.mixer_qubit_connectivity,
                                                 coeffs=self.circuit_properties.mixer_coeffs)

        self.circuit_params = QAOACircuitParams(
            self.cost_hamil, self.mixer_hamil, p=self.circuit_properties.p)
        self.variate_params = create_qaoa_variational_params(qaoa_circuit_params=self.circuit_params,
                                                             params_type=self.circuit_properties.param_type,
                                                             init_type=self.circuit_properties.init_type, 
                                                             variational_params_dict=self.circuit_properties.variational_params_dict,
                                                             linear_ramp_time=self.circuit_properties.linear_ramp_time, 
                                                             q=self.circuit_properties.q, 
                                                             seed=self.circuit_properties.seed,
                                                             total_annealing_time=self.circuit_properties.annealing_time)

        self.backend = get_qaoa_backend(circuit_params=self.circuit_params,
                                        device=self.device,
                                        **self.backend_properties.__dict__)
        self.optimizer = get_optimizer(vqa_object=self.backend,
                                       variational_params=self.variate_params,
                                       optimizer_dict=self.classical_optimizer.asdict())

        self.compiled = True
        
        if verbose:
            print('\t \033[1m ### Summary ###\033[0m')
            print(f'OpenQAOA has been compiled with the following properties')
            print(
                f'Solving QAOA with \033[1m {self.device.device_name} \033[0m on  \033[1m{self.device.device_location}\033[0m')
            print(f'Using p={self.circuit_properties.p} with {self.circuit_properties.param_type} parameters initialized as {self.circuit_properties.init_type}')

            if self.device.device_name == 'vectorized':
                print(
                    f'OpenQAOA will optimize using \033[1m{self.classical_optimizer.method}\033[0m, with up to \033[1m{self.classical_optimizer.maxiter}\033[0m maximum iterations')

            else:
                print(
                    f'OpenQAOA will optimize using \033[1m{self.classical_optimizer.method}\033[0m, with up to \033[1m{self.classical_optimizer.maxiter}\033[0m maximum iterations. Each iteration will contain \033[1m{self.backend_properties.n_shots} shots\033[0m')
                print(
                    f'The total number of shots is set to maxiter*shots = {self.classical_optimizer.maxiter*self.backend_properties.n_shots}')

        return None

    def optimize(self, verbose=False):
        '''
        A method running the classical optimisation loop
        '''

        if self.compiled == False:
            raise ValueError('Please compile the QAOA before optimizing it!')

        self.optimizer.optimize()
        # TODO: results and qaoa_results will differ
        self.results = self.optimizer.qaoa_result

        if verbose:
            print(f'optimization completed.')
        return


class RQAOA(Optimizer):
    """
    A class implementing a RQAOA workflow end to end.

    It's basic usage consists of 
    1. Initialization
    2. Compilation
    3. Optimization

    .. note::
        The attributes of the RQAOA class should be initialized using the set methods of QAOA. For example, to set the qaoa circuit's depth to 10 you should run `set_circuit_properties(p=10)`

    Attributes
    ----------
        device: `DeviceBase`
            Device to be used by the optimizer
        backend_properties: `BackendProperties`
            The backend properties of the RQAOA workflow. These properties will be used to run QAOA at each RQAOA step.
            Use to set the backend properties such as the number of shots and the cvar values.
            For a complete list of its parameters and usage please see the method set_backend_properties
        classical_optimizer: `ClassicalOptimizer`
            The classical optimiser properties of the RQAOA workflow. 
            Use to set the classical optimiser needed for the classical optimisation part of the QAOA routine.
            For a complete list of its parameters and usage please see the method set_classical_optimizer
        local_simulators: `list[str]`
            A list containing the available local simulators
        cloud_provider: `list[str]`
            A list containing the available cloud providers
        compiled: `Bool`
            A boolean flag to check whether the optimizer object has been correctly compiled at least once
        circuit_properties: `CircuitProperties`
            The circuit properties of the RQAOA workflow. These properties will be used to run QAOA at each RQAOA step.
            Use to set depth `p`, choice of parametrisation, parameter initialisation strategies, mixer hamiltonians.
            For a complete list of its parameters and usage please see the method set_circuit_properties
        rqaoa_parameters: `RqaoaParameters`
            Set of parameters containing all the relevant information for the recursive procedure of RQAOA.
        results: `RQAOAResults`
            The results of the RQAOA optimization. 
            Dictionary containing all the information about the RQAOA run: the
            solution states and energies (key: 'solution'), the output of the classical 
            solver (key: 'classical_output'), the elimination rules for each step
            (key: 'elimination_rules'), the number of eliminations at each step (key: 'schedule'), 
            total number of steps (key: 'number_steps'), the intermediate QUBO problems and the 
            intermediate QAOA objects that have been optimized in each RQAOA step (key: 'intermediate_problems').
            This object (`RQAOAResults`) is a dictionary with some custom methods as RQAOAResults.get_hamiltonian_step(i) 
            which get the hamiltonian of reduced problem of the i-th step. To see the full list of methods please see the
            RQAOAResults class.  

    Examples
    --------
    Examples should be written in doctest format, and should illustrate how
    to use the function.

    >>> r = RQAOA()
    >>> r.compile(QUBO)
    >>> r.optimise()

    Where `QUBO` is a an instance of `openqaoa.problems.problem.QUBO`

    If you want to use non-default parameters:

    Standard/custom (default) type:
    >>> r = QAOA()
    >>> r.set_circuit_properties(p=10, param_type='extended', init_type='ramp', mixer_hamiltonian='x')
    >>> r.set_device_properties(device_location='qcs', device_name='Aspen-11', cloud_credentials={'name' : "Aspen11", 'as_qvm':True, 'execution_timeout' : 10, 'compiler_timeout':10})
    >>> r.set_backend_properties(n_shots=200, cvar_alpha=1)
    >>> r.set_classical_optimizer(method='nelder-mead', maxiter=2)
    >>> r.set_rqaoa_parameters(n_cutoff = 5, steps=[1,2,3,4,5])
    >>> r.compile(qubo_problem)
    >>> r.optimize()

    Ada-RQAOA:
    >>> r_adaptive = QAOA()
    >>> r_adaptive.set_circuit_properties(p=10, param_type='extended', init_type='ramp', mixer_hamiltonian='x')
    >>> r_adaptive.set_device_properties(device_location='qcs', device_name='Aspen-11', cloud_credentials={'name' : "Aspen11", 'as_qvm':True, 'execution_timeout' : 10, 'compiler_timeout':10})
    >>> r_adaptive.set_backend_properties(n_shots=200, cvar_alpha=1)
    >>> r_adaptive.set_classical_optimizer(method='nelder-mead', maxiter=2)
    >>> r_adaptive.set_rqaoa_parameters(rqaoa_type = 'adaptive', n_cutoff = 5, n_max=5)
    >>> r_adaptive.compile(qubo_problem)
    >>> r_adaptive.optimize()
    """

    def __init__(self, device: DeviceBase=DeviceLocal('vectorized')):
        """
        Initialize the RQAOA class.

        Parameters
        ----------
            device: `DeviceBase`
                Device to be used by the optimizer. Default is using the local 'vectorized' simulator.
        """
        super().__init__(device) # use the parent class to initialize 
        self.circuit_properties = CircuitProperties()
        self.rqaoa_parameters = RqaoaParameters()

        # varaible that will store results object (when optimize is called)
        self.results = RQAOAResults()

    def set_circuit_properties(self, **kwargs): 
        """
        Specify the circuit properties to construct the QAOA circuits

        Parameters
        ----------
            qubit_register: `list`
                Select the desired qubits to run the QAOA program. Meant to be used as a qubit
                selector for qubits on a QPU. Defaults to a list from 0 to n-1 (n = number of qubits)
            p: `int`
                Depth `p` of the QAOA circuit
            q: `int`
                Analogue of `p` of the QAOA circuit in the Fourier parameterisation
            param_type: `str`
                Choose the QAOA circuit parameterisation. Currently supported parameterisations include:
                `'standard'`: Standard QAOA parameterisation
                `'standard_w_bias'`: Standard QAOA parameterisation with a separate parameter for single-qubit terms.
                `'extended'`: Individual parameter for each qubit and each term in the Hamiltonian.
                `'fourier'`: Fourier circuit parameterisation
                `'fourier_extended'`: Fourier circuit parameterisation with individual parameter for each qubit and term in Hamiltonian.
                `'fourier_w_bias'`: Fourier circuit parameterisation with aseparate parameter for single-qubit terms
            init_type: `str`
                Initialisation strategy for the QAOA circuit parameters. Allowed init_types:
                `'rand'`: Randomly initialise circuit parameters
                `'ramp'`: Linear ramp from Hamiltonian initialisation of circuit parameters (inspired from Quantum Annealing)
                `'custom'`: User specified initial circuit parameters
            mixer_hamiltonian: `str`
                Parameterisation of the mixer hamiltonian:
                `'x'`: Randomly initialise circuit parameters
                `'xy'`: Linear ramp from Hamiltonian initialisation of circuit 
            mixer_qubit_connectivity: `[Union[List[list],List[tuple], str]]`
                The connectivity of the qubits in the mixer Hamiltonian. Use only if `mixer_hamiltonian = xy`. The user can specify the 
                connectivity as a list of lists, a list of tuples, or a string chosen from ['full', 'chain', 'star'].
            mixer_coeffs: `list`
                The coefficients of the mixer Hamiltonian. By default all set to -1
            annealing_time: `float`
                Total time to run the QAOA program in the Annealing parameterisation (digitised annealing)
            linear_ramp_time: `float`
                The slope(rate) of linear ramp initialisation of QAOA parameters.
            variational_params_dict: `dict`
                Dictionary object specifying the initial value of each circuit parameter for the chosen parameterisation, if the `init_type` is selected as `'custom'`.    
                For example, for standard parametrisation set {'betas': [0.1, 0.2, 0.3], 'gammas': [0.1, 0.2, 0.3]}
        """

        for key in kwargs.keys():
            if hasattr(self.circuit_properties, key):
                pass
            else:
                raise ValueError(
                    f"Specified argument {key} is not supported by the circuit")

        self.circuit_properties = CircuitProperties(**kwargs) 

        return None

    def set_rqaoa_parameters(self, **kwargs):
        """
        Specify the parameters to run a desired RQAOA program.

        Parameters
        ----------
        rqaoa_type: `int`
            String specifying the RQAOA scheme under which eliminations are computed. The two methods are 'custom' and
            'adaptive'. Defaults to 'custom'.
        n_max: `int`
            Maximum number of eliminations allowed at each step when using the adaptive method.
        steps: `Union[list,int]`
            Elimination schedule for the RQAOA algorithm. If an integer is passed, it sets the number of spins eliminated
            at each step. If a list is passed, the algorithm will follow the list to select how many spins to eliminate 
            at each step. Note that the list needs enough elements to specify eliminations from the initial number of qubits
            up to the cutoff value. If the list contains more, the algorithm will follow instructions until the cutoff value 
            is reached.
        n_cutoff: `int`
            Cutoff value at which the RQAOA algorithm obtains the solution classically.
        original_hamiltonian: `Hamiltonian`
            Hamiltonian encoding the original problem fed into the RQAOA algorithm.
        counter: `int`
            Variable to count the step in the schedule. If counter = 3 the next step is schedule[3]. 
            Default is 0, but can be changed to start in the position of the schedule that one wants.
        """

        for key in kwargs.keys():
            if hasattr(self.rqaoa_parameters, key):
                pass
            else:
                raise ValueError(
                    f'Specified argument {key} is not supported by RQAOA')

        self.rqaoa_parameters = RqaoaParameters(**kwargs) 

        return None

    def compile(self, problem: QUBO = None, verbose: bool = False):
        """
        Create a QAOA object and initialize it with the circuit properties, device, classical optimizer and
        backend properties specified by the user.
        This QAOA object will be used to run QAOA changing the problem to sove at each RQAOA step. 
        Here, the QAOA is compiled passing the problem statement, so to check that the compliation of 
        QAOA is correct. See the QAOA class.

        .. note::
            Compilation is necessary because it is the moment where the problem statement and the QAOA instructions are used to build the actual QAOA circuit.

        Parameters
        ----------
        problem: `Problem`
            QUBO problem to be solved by RQAOA
        verbose: bool
            !NotYetImplemented! Set true to have a summary of QAOA first step to displayed after compilation
        """

        # save the original problem
        self.problem = problem 

        # if type is custom and steps is an int, set steps correctly
        if self.rqaoa_parameters.rqaoa_type == "custom" and self.rqaoa_parameters.n_cutoff<=problem.n:

            n_cutoff = self.rqaoa_parameters.n_cutoff
            n_qubits = problem.n
            counter  = self.rqaoa_parameters.counter

            # If schedule for custom RQAOA is not given, we create a schedule such that 
            # n = self.rqaoa_parameters.steps spins is eliminated at a time
            if type(self.rqaoa_parameters.steps) is int:
                self.rqaoa_parameters.steps = [self.rqaoa_parameters.steps]*(n_qubits-n_cutoff)
            
            # In case a schedule is given, ensure there are enough steps in the schedule
            assert np.abs(n_qubits - n_cutoff - counter) <= sum(self.rqaoa_parameters.steps),\
                f"Schedule is incomplete, add {np.abs(n_qubits - n_cutoff - counter) - sum(self.rqaoa_parameters.steps)} more eliminations"


        # Create the qaoa object with the properties
        self._q = QAOA(self.device)
        self._q.circuit_properties  = self.circuit_properties
        self._q.backend_properties  = self.backend_properties
        self._q.classical_optimizer = self.classical_optimizer

        # compile qaoa object
        self._q.compile(problem, verbose=verbose)

        self.compiled = True

        return 

    def optimize(self, verbose=False):
        """
        Performs optimization using RQAOA with the `custom` method or the `adaptive` method.
        The elimination RQAOA loop will occur until the number of qubits is equal to the number of qubits specified in `n_cutoff`.
        In each loop, the QAOA will be run, then the eliminations will be computed, a new problem will be redefined
        and the QAOA will be recompiled with the new problem.
        Once the loop is complete, the final problem will be solved classically and the final solution will be reconstructed.
        Results will be stored in the `results` attribute.
        """

        # lists to append the eliminations and the qaoa objects
        elimination_tracker = []
        qaoa_steps = []
        problem_steps = []
        
        exp_vals_z_all = []
        corr_matrix_all = []

        # get variables
        problem = self.problem  
        n_cutoff = self.rqaoa_parameters.n_cutoff
        n_qubits = problem.n
        counter = self.rqaoa_parameters.counter

        # copy the original qaoa object
        q = copy.deepcopy(self._q)

        # create a different max_terms function for each type 
        if self.rqaoa_parameters.rqaoa_type == "adaptive":
            f_max_terms = rqaoa.ada_max_terms  
        else:
            f_max_terms = rqaoa.max_terms 

        # If above cutoff, loop quantumly, else classically
        while n_qubits > n_cutoff:

            # Run QAOA
            q.optimize()

            # Obtain statistical results
            exp_vals_z, corr_matrix = self._exp_val_hamiltonian_termwise(q)
            exp_vals_z_all.append(exp_vals_z)
            corr_matrix_all.append(corr_matrix)
            # Retrieve highest expectation values according to adaptive method or schedule in custom method
            max_terms_and_stats = f_max_terms(exp_vals_z, corr_matrix, self._n_step(n_qubits, n_cutoff, counter))
            # Generate spin map
            spin_map = rqaoa.spin_mapping(problem, max_terms_and_stats)
            # Eliminate spins and redefine problem
            new_problem, spin_map = rqaoa.redefine_problem(problem, spin_map)

            # Extract final set of eliminations with correct dependencies and update tracker
            eliminations = {(spin_map[spin][1],spin):spin_map[spin][0] for spin in sorted(spin_map.keys()) if spin != spin_map[spin][1]}
            elimination_tracker.append(eliminations)

            # Extract new number of qubits
            n_qubits = new_problem.n

            # Save qaoa object and new problem
            qaoa_steps.append(copy.deepcopy(q))
            problem_steps.append(copy.deepcopy(new_problem))

            # problem is updated
            problem = new_problem
            
            # Compile qaoa with the problem
            q.compile(problem, verbose=False)

            # Add one step to the counter
            counter += 1

        # Solve the new problem classically
        cl_energy, cl_ground_states = ground_state_hamiltonian(problem.hamiltonian)

        # Retrieve full solutions including eliminated spins and their energies
        full_solutions = rqaoa.final_solution(
            elimination_tracker, cl_ground_states, self.problem.hamiltonian)

        # Compute description dictionary containing all the information            
        self.results['solution'] = full_solutions
        self.results['classical_output'] = {'minimum_energy': cl_energy,  'optimal_states': cl_ground_states}
        self.results['elimination_rules'] = elimination_tracker
        self.results['schedule'] = [len(max_tc) for max_tc in elimination_tracker]
        self.results['intermediate_steps'] = [{'QUBO': problem, 'QAOA': qaoa} for qaoa, problem in zip(qaoa_steps, problem_steps)]
        self.results['number_steps'] = counter - self.rqaoa_parameters.counter 
        self.results['intermediate_exp_vals_z'] = exp_vals_z_all
        self.results['intermediate_corr_matrix'] = corr_matrix_all

        if verbose:
            print(f'RQAOA optimization completed.')

        return 


    def _exp_val_hamiltonian_termwise(self, q):
        """
        Private method to call the exp_val_hamiltonian_termwise function taking the data from
        the QAOA object _q. 
        It eturns what the exp_val_hamiltonian_termwise function returns.
        """

        variational_params = q.variate_params
        qaoa_backend = q.backend
        cost_hamiltonian = q.cost_hamil
        mixer_type = q.circuit_properties.mixer_hamiltonian
        p = q.circuit_properties.p
        qaoa_optimized_angles = q.results.optimized['optimized angles']
        qaoa_optimized_counts = q.results.get_counts(q.results.optimized['optimized measurement outcomes'])
        analytical = isinstance(qaoa_backend, QAOABaseBackendStatevector)
    
        return exp_val_hamiltonian_termwise(variational_params, 
                qaoa_backend, cost_hamiltonian, mixer_type, p, qaoa_optimized_angles, 
                qaoa_optimized_counts, analytical=analytical)


    def _n_step(self, n_qubits, n_cutoff, counter):
        """
        Private method that returns the n_max value in case of adaptive or the number of eliminations according 
        to the schedule and the counter in case of custom method.
        """

        if self.rqaoa_parameters.rqaoa_type == "adaptive":
            # Number of spins to eliminate according the schedule
            n = self.rqaoa_parameters.n_max
        else:
            # max Number of spins to eliminate
            n = self.rqaoa_parameters.steps[counter]

        # If the step eliminates more spins than available, reduce step to match cutoff
        return (n_qubits - n_cutoff) if (n_qubits - n_cutoff) < n else n