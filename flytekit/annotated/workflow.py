import inspect
from typing import Dict, Any

from flytekit import engine as flytekit_engine, logger
from flytekit.annotated.context_manager import FlyteContext, ExecutionState
from flytekit.annotated.promise import Promise
from flytekit.annotated.interface import transform_variable_map, extract_return_annotation, \
    transform_signature_to_interface, transform_interface_to_typed_interface
from flytekit.common import constants as _common_constants
from flytekit.common.promise import NodeOutput as _NodeOutput
from flytekit.common.workflow import SdkWorkflow as _SdkWorkflow
from flytekit.models import literals as _literal_models, interface as _interface_models, types as _type_models
from flytekit.models.core import identifier as _identifier_model


class Workflow(object):
    """
    When you assign a name to a node.
    * Any upstream node that is not assigned, recursively assign
    * When you get the call to the constructor, keep in mind there may be duplicate nodes, because they all should
      be wrapper nodes.
    """

    def __init__(self, workflow_function, sdk_workflow):
        self._workflow_function = workflow_function
        self._sdk_workflow = sdk_workflow

    def _local_execute(self, ctx: FlyteContext, **kwargs) -> Dict[str, Any]:
        """
        Performs local execution of a workflow
        """
        # Assume that the inputs given are given as Python native values
        # Let's translate these Python native values into Flyte IDL literals - This is normally done when
        # you run a workflow for real also.
        try:
            inputs_as_dict_of_literals = {
                k: flytekit_engine.python_value_to_idl_literal(ctx, v,
                                                               self._sdk_workflow.interface.inputs[k].type)
                for k, v in kwargs.items()
            }
        except Exception as e:
            # TODO: Why doesn't this print a stack trace?
            logger.warning("Exception!!!")
            raise e

        inputs_as_wrapped_promises = {
            k: Promise(var=k, val=v) for k, v in
            inputs_as_dict_of_literals.items()
        }

        # TODO: These are all assumed to be TaskCallOutput objects, but they can
        #   be other things as well.  What if someone just returns 5? Should we disallow this?
        function_outputs = self._workflow_function(**inputs_as_wrapped_promises)
        output_names = list(self._sdk_workflow.interface.outputs.keys())
        output_literal_map = {}
        # TODO Ketan fix this make it into a simple promise transformation
        if len(output_names) > 1:
            for idx, var_name in enumerate(output_names):
                output_literal_map[var_name] = function_outputs[idx].val
        elif len(output_names) == 1:
            output_literal_map[output_names[0]] = function_outputs.val
        else:
            return None

        return flytekit_engine.idl_literal_map_to_python_value(ctx, _literal_models.LiteralMap(
            literals=output_literal_map))

    def __call__(self, *args, **kwargs):

        if len(args) > 0:
            raise Exception('not allowed')

        ctx = FlyteContext.current_context()
        # Reserved for when we have subworkflows
        if ctx.compilation_state is not None:
            raise Exception('not implemented')

        # When someone wants to run the workflow function locally
        else:
            with ctx.new_execution_context(mode=ExecutionState.Mode.LOCAL_WORKFLOW_EXECUTION) as ctx:
               return self._local_execute(ctx, **kwargs)


def workflow(_workflow_function=None):
    # Unlike for tasks, where we can determine the entire structure of the task by looking at the function's signature,
    # workflows need to have the body of the function itself run at module-load time. This is because the body of the
    # workflow is what expresses the workflow structure.
    def wrapper(fn):
        sig = inspect.signature(fn)
        interface = transform_signature_to_interface(sig)
        interface = transform_interface_to_typed_interface(interface)

        # Create promises out of all the inputs. Check for defaults in the function definition.
        default_inputs = {
            k: v.default
            for k, v in sig.parameters.items()
            if v.default is not inspect.Parameter.empty
        }

        input_parameter_models = []
        for input_name, input_variable_obj in interface.inputs.items():
            # TODO: Fix defaults and required
            parameter_model = _interface_models.Parameter(var=input_variable_obj, default=None, required=True)
            input_parameter_models.append(parameter_model)

        all_nodes = []
        ctx = FlyteContext.current_context()
        with ctx.new_compilation_context() as comp_ctx:
            # Fill in call args by constructing input bindings
            input_kwargs = {
                k: _type_models.OutputReference(_common_constants.GLOBAL_INPUT_NODE_ID, k) for k in
                interface.inputs.keys()
            }
            workflow_outputs = fn(**input_kwargs)
            all_nodes.extend(comp_ctx.compilation_state.nodes)

        # Iterate through the workflow outputs
        #  Get the outputs and use them to construct the old Output objects
        #    promise.NodeOutputs (let's just focus on this one first for POC)
        #    or Input objects from above in the case of a passthrough value
        #    or outputs can be like 5, or 'hi'
        # These should line up with the output input argument
        # TODO: Add length checks.
        bindings = []
        output_names = list(interface.outputs.keys())
        if len(output_names) > 0:
            for i, out in enumerate(workflow_outputs):
                output_name = output_names[i]
                # TODO: Check that the outputs returned type match the interface.
                # output_literal_type = out.literal_type
                # logger.debug(f"Got output wrapper: {out}")
                # logger.debug(f"Var name {output_name} wf output name {outputs[i]} type: {output_literal_type}")
                binding_data = _literal_models.BindingData(promise=out)
                bindings.append(_literal_models.Binding(var=output_name, binding=binding_data))

        # TODO: Again, at this point, we should be able to identify the name of the workflow
        workflow_id = _identifier_model.Identifier(_identifier_model.ResourceType.WORKFLOW,
                                                   "proj", "dom", "moreblah", "1")

        # Create a FlyteWorkflow object. We call this like how promote_from_model would call this, by ignoring the
        # fancy arguments and supplying just the raw elements manually. Alternatively we can construct the
        # WorkflowTemplate object, and then call promote_from_model.
        sdk_workflow = _SdkWorkflow(inputs=None, outputs=None, nodes=all_nodes, id=workflow_id, metadata=None,
                                    metadata_defaults=None, interface=interface, output_bindings=bindings)
        # logger.debug(f"SdkWorkflow {sdk_workflow}")

        workflow_instance = Workflow(fn, sdk_workflow)
        workflow_instance.id = workflow_id

        return workflow_instance

    if _workflow_function:
        return wrapper(_workflow_function)
    else:
        return wrapper


def get_default_args(func):
    """
    Returns the default arguments to a function as a dict. Will be empty if there are none.
    """
    signature = inspect.signature(func)
    return {
        k: v.default
        for k, v in signature.parameters.items()
        if v.default is not inspect.Parameter.empty
    }
