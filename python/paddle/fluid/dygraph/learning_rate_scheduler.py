# Copyright (c) 2016 PaddlePaddle Authors. All Rights Reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
import warnings
import numpy as np

import paddle
from .. import unique_name
from ..framework import Variable
from ..data_feeder import check_type

__all__ = [
    'NoamDecay',
    'PolynomialDecay',
    'LinearLrWarmup',
    'ReduceLROnPlateau',
]


class LearningRateDecay:
    """
    Base class of learning rate decay

    Define the common interface of an LearningRateDecay.
    User should not use this class directly,
    but need to use one of it's implementation.
    """

    def __init__(self, begin=0, step=1, dtype='float32'):
        self.step_num = begin
        self.step_size = step
        self.dtype = dtype

    def __call__(self):
        lr = self.step()
        if isinstance(lr, float):
            lr = self.create_lr_var(lr)
        self.step_num += self.step_size
        return lr

    def create_lr_var(self, lr):
        """
        convert lr from float to variable

        Args:
            lr: learning rate
        Returns:
            learning rate variable
        """
        from .. import layers

        lr = paddle.static.create_global_var(
            name=unique_name.generate("learning_rate"),
            shape=[1],
            value=float(lr),
            dtype=self.dtype,
            persistable=False,
        )
        return lr

    # Note: If you want to change what optimizer.state_dict stores, just overwrite this functions,
    # "self.step_num" will be stored by default.
    def state_dict(self):
        """
        Returns the state of the scheduler as a :class:`dict`.

        It is a subset of self.__dict__ .
        """
        self._state_keys()
        state_dict = {}
        for key in self.keys:
            if key not in self.__dict__:
                continue
            value = self.__dict__[key]
            if isinstance(value, Variable):
                assert (
                    value.size == 1
                ), "the size of Variable in state_dict must be 1, but its size is {} with shape {}".format(
                    value.size, value.shape
                )
                value = value.item()
            state_dict[key] = value

        return state_dict

    def _state_keys(self):
        """
        set the keys in self.__dict__ that are needed to be saved.
        """
        self.keys = ['step_num']

    def set_state_dict(self, state_dict):
        """
        Loads the schedulers state.
        """
        self._state_keys()
        for key in self.keys:
            if key in state_dict:
                self.__dict__[key] = state_dict[key]
            else:
                raise RuntimeError(
                    "Please check whether state_dict is correct for optimizer. Can't find [ {} ] in state_dict".format(
                        key
                    )
                )
        if len(state_dict) > len(self.keys):
            warnings.warn(
                "There are some unused values in state_dict. Maybe the optimizer have different 'LearningRateDecay' when invoking state_dict and set_dict"
            )

    # [aliases] Compatible with old method names
    set_dict = set_state_dict

    def step(self):
        raise NotImplementedError()


class PolynomialDecay(LearningRateDecay):
    r"""
    :api_attr: imperative

    Applies polynomial decay to the initial learning rate.

    The algorithm can be described as following.

    If cycle is set to True, then:

    .. math::

        decay\_steps & = decay\_steps * math.ceil(\\frac{global\_step}{decay\_steps})

        decayed\_learning\_rate & = (learning\_rate-end\_learning\_rate)*(1-\\frac{global\_step}{decay\_steps})^{power}+end\_learning\_rate

    If cycle is set to False, then:

    .. math::

        global\_step & = min(global\_step, decay\_steps)

        decayed\_learning\_rate & = (learning\_rate-end\_learning\_rate)*(1-\\frac{global\_step}{decay\_steps})^{power}+end\_learning\_rate

    Parameters:
        learning_rate(Variable|float): The initial learning rate. If the type
            is Variable, it's a tensor with shape [1], the data type can be
            float32 or float64. It also can be set to python int number.
        decay_steps(int): The decay step size. It determines the decay cycle.
        end_learning_rate(float, optional): The minimum final learning rate. The default value is 0.0001.
        power(float, optional): Power of polynomial. The default value is 1.0.
        cycle(bool, optional): If set true, decay the learning rate every decay_steps. The default value is False.
        begin(int, optional): The begin step. The initial value of global_step described above. The default value is 0.
        step(int, optional): The step size used to calculate the new global_step in the description above.
            The default value is 1.
        dtype(str, optional): The data type used to create the learning rate variable. The data type can be set as
            'float32', 'float64'. The default value is 'float32'.

    Returns:
        None.

    Examples:
        .. code-block:: python

          import paddle.fluid as fluid
          import paddle
          start_lr = 0.01
          total_step = 5000
          end_lr = 0
          with fluid.dygraph.guard():
              emb = paddle.nn.Embedding(10, 10)
              optimizer  = fluid.optimizer.SGD(
                  learning_rate = fluid.dygraph.PolynomialDecay(
                  start_lr, total_step, end_lr, power=1.0),
                  parameter_list = emb.parameters())

    """

    def __init__(
        self,
        learning_rate,
        decay_steps,
        end_learning_rate=0.0001,
        power=1.0,
        cycle=False,
        begin=0,
        step=1,
        dtype='float32',
    ):
        super().__init__(begin, step, dtype)
        self.learning_rate = learning_rate
        self.decay_steps = decay_steps
        self.end_learning_rate = end_learning_rate
        self.power = power
        self.cycle = cycle

    def step(self):
        tmp_step_num = self.step_num
        tmp_decay_steps = self.decay_steps
        if self.cycle:
            div_res = paddle.ceil(
                self.create_lr_var(tmp_step_num / float(self.decay_steps))
            )

            if tmp_step_num == 0:
                div_res = self.create_lr_var(1.0)
            tmp_decay_steps = self.decay_steps * div_res
        else:
            tmp_step_num = self.create_lr_var(
                tmp_step_num
                if tmp_step_num < self.decay_steps
                else self.decay_steps
            )

        decayed_lr = (self.learning_rate - self.end_learning_rate) * (
            (1 - tmp_step_num / tmp_decay_steps) ** self.power
        ) + self.end_learning_rate
        return decayed_lr


class NoamDecay(LearningRateDecay):
    r"""
    :api_attr: imperative

    Applies Noam decay to the initial learning rate.

    The algorithm can be described as following.

    .. math::

        decayed\_learning\_rate = learning\_rate * d_{model}^{-0.5} * min(global\_step^{-0.5}, global\_step * warmup\_steps^{-1.5})

    Please reference `attention is all you need <https://arxiv.org/pdf/1706.03762.pdf>`_

    Parameters:
        d$_{model}$(Variable|int): The dimensionality of input and output feature vector of model. If type is Variable,
            it's a tensor with shape [1] and the data type can be int32 or int64. The type can also be python int.
        warmup_steps(Variable|int): The number of warmup steps. A super parameter. If type is Variable,
            it's a tensor with shape [1] and the data type can be int32 or int64. The type can also be python int.
        begin(int, optional): The begin step. The initial value of global_step described above. The default value is 0.
        step(int, optional): The step size used to calculate the new global_step in the description above.
            The default value is 1.
        dtype(str, optional): The data type used to create the learning rate variable. The data type can be set as
            'float32', 'float64'. The default value is 'float32'.
        learning_rate(Variable|float|int): The initial learning rate. If the type
            is Variable, it's a tensor with shape [1], the data type can be
            float32 or float64. It also can be set to python int number. Default 1.0

    Returns:
        None.

    Examples:
        .. code-block:: python

          import paddle.fluid as fluid
          import paddle
          warmup_steps = 100
          learning_rate = 0.01
          with fluid.dygraph.guard():
              emb = paddle.nn.Embedding(10, 10)
              optimizer  = fluid.optimizer.SGD(
                  learning_rate = fluid.dygraph.NoamDecay(
                         1/(warmup_steps *(learning_rate ** 2)),
                         warmup_steps),
                  parameter_list = emb.parameters())
    """

    def __init__(
        self,
        d_model,
        warmup_steps,
        begin=1,
        step=1,
        dtype='float32',
        learning_rate=1.0,
    ):
        super().__init__(begin, step, dtype)
        self.learning_rate = learning_rate
        self.d_model = d_model
        self.warmup_steps = warmup_steps

    def step(self):
        from .. import layers

        a = self.create_lr_var(self.step_num**-0.5)
        b = self.create_lr_var((self.warmup_steps**-1.5) * self.step_num)
        lr_value = (
            self.learning_rate * (self.d_model**-0.5) * paddle.minimum(a, b)
        )
        return lr_value


class LinearLrWarmup(LearningRateDecay):
    """
    :api_attr: imperative

    This operator use the linear learning rate warm up strategy to adjust the learning rate preliminarily before the normal learning rate scheduling.
    For more information, please refer to `Bag of Tricks for Image Classification with Convolutional Neural Networks <https://arxiv.org/abs/1812.01187>`_

    When global_step < warmup_steps, learning rate is updated as:

    .. code-block:: text

            linear_step = end_lr - start_lr
            lr = start_lr + linear_step * (global_step / warmup_steps)

    where start_lr is the initial learning rate, and end_lr is the final learning rate;

    When global_step >= warmup_steps, learning rate is updated as:

    .. code-block:: text

            lr = learning_rate

    where lr is the learning_rate after warm-up.

    Args:
        learning_rate (Variable|float): Learning_rate after warm-up, it could be 1D-Tensor or single value with the data type of float32.
        warmup_steps (int): Steps for warm up.
        start_lr (float): Initial learning rate of warm up.
        end_lr (float): Final learning rate of warm up.
        begin(int, optional): The begin step. The initial value of global_step described above. The default value is 0.
        step(int, optional): The step size used to calculate the new global_step in the description above.
            The default value is 1.
        dtype(str, optional): The data type used to create the learning rate variable. The data type can be set as
            'float32', 'float64'. The default value is 'float32'.

    Returns:
        Variable: Warm-up learning rate with the same data type as learning_rate.


    Examples:

    .. code-block:: python

        import paddle.fluid as fluid

        learning_rate = 0.1
        warmup_steps = 50
        start_lr = 0
        end_lr = 0.1

        with fluid.dygraph.guard():
            lr_decay = fluid.dygraph.LinearLrWarmup( learning_rate, warmup_steps, start_lr, end_lr)


    """

    def __init__(
        self,
        learning_rate,
        warmup_steps,
        start_lr,
        end_lr,
        begin=1,
        step=1,
        dtype='float32',
    ):
        super().__init__(begin, step, dtype)
        type_check = (
            isinstance(learning_rate, float)
            or isinstance(learning_rate, int)
            or isinstance(learning_rate, LearningRateDecay)
        )
        if not type_check:
            raise TypeError(
                "the type of learning_rate should be [int, float or LearningRateDecay], the current type is {}".format(
                    learning_rate
                )
            )
        self.learning_rate = learning_rate
        self.warmup_steps = warmup_steps
        self.start_lr = start_lr
        assert (
            end_lr > start_lr
        ), "end_lr {} must be greater than start_lr {}".format(end_lr, start_lr)
        self.lr_ratio_before_warmup = (float(end_lr) - float(start_lr)) / float(
            warmup_steps
        )

    def step(self):
        base_lr = self.learning_rate
        if isinstance(self.learning_rate, LearningRateDecay):
            base_lr = base_lr()

        from .. import layers

        if self.step_num < self.warmup_steps:
            return self.lr_ratio_before_warmup * self.step_num + self.start_lr
        else:
            return base_lr


class ReduceLROnPlateau(LearningRateDecay):
    """
    :api_attr: imperative

    Reduce learning rate when ``loss`` has stopped descending. Models often benefit from reducing the learning rate
    by 2 to 10 times once model performance has no longer improvement.

    The ``loss`` is the one which has been pass into ``step`` , it must be 0-D Tensor with shape []. When ``loss``
    stop descending for a ``patience`` number of epochs, the learning rate will be reduced to ``learning_rate * decay_rate`` .
    (Specially, ``mode`` can also be set to ``'max`` , in this case, when ``loss`` stop ascending for a ``patience`` number
    of epochs, the learning rate will be reduced.)

    In addition, After each reduction, it will wait a ``cooldown`` number of epochs before resuming normal operation.

    Args:
        learning_rate (Variable|float|int): The initial learning rate. It can be set to python float or int number.
            If the type is Variable, it should be 1-D Tensor with shape [1], the data type can be 'float32' or 'float64'.
        mode (str, optional): ``'min'`` or ``'max'`` can be selected. Normally, it is ``'min'`` , which means that the
            learning rate will reduce when ``loss`` stops descending. Specially, if it's set to ``'max'`` ,  the learning
            rate will reduce when ``loss`` stops ascending. Default: ``'min'`` .
        decay_rate (float, optional): The Ratio that the learning rate will be reduced. ``new_lr = origin_lr * decay_rate`` .
            It should be less than 1.0. Default: 0.1.
        patience (int, optional): When ``loss`` doesn't improve for this number of epochs, learing rate will be reduced.
            Default: 10.
        verbose (bool, optional): If ``True``, prints a message to stdout for each update. Default: ``False``.
        threshold (float, optional): ``threshold`` and ``threshold_mode`` will determine the minimum change of ``loss`` .
            This make tiny changes of ``loss`` will be ignored. Default: 1e-4.
        threshold_mode (str, optional): ``'rel'`` or ``'abs'`` can be selected. In ``'rel'`` mode, the minimum change of ``loss``
            is ``last_loss * threshold`` , where ``last_loss`` is ``loss`` in last epoch. In ``'abs'`` mode, the minimum
            change of ``loss`` is ``threshold`` . Default: ``'rel'`` .
        cooldown (int, optional): The number of epochs to wait before resuming normal operation. Default: 0.
        min_lr (float, optional): The lower bound of the learning rate after reduction. Default: 0.
        eps (float, optional): Minimal decay applied to lr. If the difference between new and old lr is smaller than eps, the update is
            ignored. Default: 1e-8.
        dtype (str, optional): The data type used to create the learning rate variable. The data type can be set as
            'float32', 'float64'. Default: 'float32'.

    Returns:
        Reduced learning rate.

    Examples:

    .. code-block:: python

        import paddle.fluid as fluid
        import paddle
        import numpy as np

        with fluid.dygraph.guard():
            x = np.random.uniform(-1, 1, [10, 10]).astype("float32")
            linear = paddle.nn.Linear(10, 10)
            input = fluid.dygraph.to_variable(x)

            reduce_lr = fluid.dygraph.ReduceLROnPlateau(
                                    learning_rate = 1.0,
                                    decay_rate = 0.5,
                                    patience = 5,
                                    verbose = True,
                                    cooldown = 3)
            adam = fluid.optimizer.Adam(
                learning_rate = reduce_lr,
                parameter_list = linear.parameters())

            for epoch in range(10):
                total_loss = 0
                for bath_id in range(5):
                    out = linear(input)
                    loss = paddle.mean(out)
                    total_loss += loss
                    adam.minimize(loss)

                avg_loss = total_loss/5

                # adjust learning rate according to avg_loss
                reduce_lr.step(avg_loss)
                lr = adam.current_step_lr()
                print("current avg_loss is %s, current lr is %s" % (float(avg_loss), lr))

    """

    def __init__(
        self,
        learning_rate,
        mode='min',
        decay_rate=0.1,
        patience=10,
        verbose=False,
        threshold=1e-4,
        threshold_mode='rel',
        cooldown=0,
        min_lr=0,
        eps=1e-8,
        dtype='float32',
    ):
        super().__init__(dtype=dtype)
        mode = mode.lower()
        if mode not in ['min', 'max']:
            raise ValueError('mode ' + mode + ' is unknown!')
        self.mode = mode

        if decay_rate >= 1.0:
            raise ValueError(
                'new_lr = origin_lr * decay_rate and decay_rate should be < 1.0.'
            )
        self.decay_rate = self.create_lr_var(decay_rate)

        threshold_mode = threshold_mode.lower()
        if threshold_mode not in ['rel', 'abs']:
            raise ValueError(
                'threshold mode ' + threshold_mode + ' is unknown!'
            )
        self.threshold_mode = threshold_mode
        check_type(
            learning_rate,
            'learning_rate',
            (float, int, Variable),
            'ReduceLROnPlateau',
        )
        if not isinstance(learning_rate, (float, int, Variable)):
            raise TypeError(
                "The type of 'learning_rate' in 'ReduceLROnPlateau' must be 'float, int, Variable', but received %s."
                % type(learning_rate)
            )

        self.learning_rate = learning_rate
        self.verbose = verbose
        self.patience = patience
        self.threshold = threshold
        self.threshold_mode = threshold_mode
        self.cooldown = cooldown
        self.min_lr = self.create_lr_var(min_lr)
        self.eps = eps

        self.cooldown_counter = 0
        self.best_loss = None
        self.num_bad_epochs = 0
        self.epoch_num = 0

    # "cooldown_counter / best_loss / num_bad_epochs / epoch_num / learning_rate" will be stored.
    def _state_keys(self):
        self.keys = [
            'cooldown_counter',
            'best_loss',
            'num_bad_epochs',
            'epoch_num',
            'learning_rate',
        ]

    def __call__(self):
        if not isinstance(self.learning_rate, Variable):
            self.learning_rate = self.create_lr_var(self.learning_rate)
        return self.learning_rate

    def step(self, loss):
        """
        It should be invoked on each epoch. Update the learning rate in optimizer according to ``loss`` .
        The new learning rate will take effect on next call to ``optimizer.minimize`` .

        Args:
            loss (Variable): A ``Variable`` that will be monitored to determine whether the learning rate will reduce.
                If it stop descending for a ``patience`` number of epochs, the learning rate will reduce. It should
                be 0-D Tensor with shape [].
                Specially, if ``mode`` has been set to ``'max'`` ,  the learning rate will reduce when it stops ascending.
        Returns:
            None

        Examples:
            Please refer to the example of current LearningRateDecay.
        """

        # loss.size must be 1
        check_type(loss, 'loss', Variable, 'ReduceLROnPlateau.step')
        assert np.prod(loss.shape) == 1, (
            "The number of elements of loss should be 1, but the current loss.shape is {}, whose number of elements is not 1. "
            "Maybe that you should call paddle.mean to process it first.".format(
                loss.shape
            )
        )

        self.epoch_num += 1
        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
        else:
            if self.best_loss is None or self._is_better(loss, self.best_loss):
                self.best_loss = loss
                self.num_bad_epochs = 0
            else:
                self.num_bad_epochs += 1

            if self.num_bad_epochs > self.patience:
                self.cooldown_counter = self.cooldown
                self.num_bad_epochs = 0
                new_lr = paddle.maximum(
                    self.learning_rate * self.decay_rate, self.min_lr
                )
                if self.learning_rate - new_lr > self.eps:
                    if self.verbose:
                        print(
                            'Epoch {}: reducing learning rate from {} to {}.'.format(
                                self.epoch_num,
                                float(self.learning_rate),
                                float(new_lr),
                            )
                        )
                    self.learning_rate = new_lr

    def _is_better(self, current, best):
        if self.mode == 'min' and self.threshold_mode == 'rel':
            return current < best - best * self.threshold

        elif self.mode == 'min' and self.threshold_mode == 'abs':
            return current < best - self.threshold

        elif self.mode == 'max' and self.threshold_mode == 'rel':
            return current > best + best * self.threshold

        else:
            return current > best + self.threshold


class _LearningRateEpochDecay(LearningRateDecay):
    """
    :api_attr: imperative

    Base class of learning rate decay, which is updated each epoch.

    Define the common interface of an _LearningRateEpochDecay.
    User should not use this class directly,
    but need to use one of it's implementation. And invoke method: `epoch()` each epoch.
    """

    def __init__(self, learning_rate, dtype=None):
        if not isinstance(learning_rate, (float, int)):
            raise TypeError(
                "The type of 'learning_rate' must be 'float, int', but received %s."
                % type(learning_rate)
            )
        if learning_rate < 0:
            raise ValueError("Invalid learning rate: {}".format(learning_rate))

        self.base_lr = float(learning_rate)

        self.epoch_num = -1
        self.dtype = dtype
        if dtype is None:
            self.dtype = "float32"
        self.learning_rate = self.create_lr_var(self.base_lr)

        self.epoch()

    # For those subclass who overload _LearningRateEpochDecay, "self.epoch_num/learning_rate" will be stored by default.
    # you can change it for your subclass.
    def _state_keys(self):
        self.keys = ['epoch_num', 'learning_rate']

    def __call__(self):
        """
        Return last computed learning rate on current epoch.
        """
        if not isinstance(self.learning_rate, Variable):
            self.learning_rate = self.create_lr_var(self.learning_rate)
        return self.learning_rate

    def epoch(self, epoch=None):
        """
        compueted learning_rate and update it when invoked.
        """
        if epoch is None:
            self.epoch_num += 1
        else:
            self.epoch_num = epoch

        self.learning_rate = self.get_lr()

    def get_lr(self):
        raise NotImplementedError
