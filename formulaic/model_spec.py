from __future__ import annotations

import inspect
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union, TYPE_CHECKING

from formulaic.materializers.base import EncodedTermStructure
from formulaic.parser.types import Structured, Term
from formulaic.utils.constraints import LinearConstraintSpec, LinearConstraints

from .formula import Formula
from .materializers import FormulaMaterializer, NAAction

if TYPE_CHECKING:  # pragma: no cover
    from .model_matrix import ModelMatrices, ModelMatrix

# Cached property was introduced in Python 3.8 (we currently support 3.7)
try:
    from functools import cached_property
except ImportError:  # pragma: no cover
    from cached_property import cached_property


@dataclass(frozen=True)
class ModelSpec:
    """
    A container for the metadata used to generate a `ModelMatrix` instance.

    This object can also be used to create a `ModelMatrix` instance that
    respects the encoding choices made during the generation of this `ModelSpec`
    instance.

    Attributes:
        Configuration:
            formula: The formula for which the model matrix was (and/or will be)
                generated.
            materializer: The materializer used (and/or to be used) to
                materialize the formula into a matrix.
            ensure_full_rank: Whether to ensure that the generated matrix is
                "structurally" full-rank (features are not included which are
                known to violate full-rankness).
            na_action: The action to be taken if NA values are found in the
                data. Can be on of: "drop" (the default), "raise" or "ignore".
            output: The desired output type (as interpreted by the materializer;
                e.g. "pandas", "sparse", etc).

        State (these attributes are only populated during materialization):
            structure: The model matrix structure resulting from materialization.
            transform_state: The state of any stateful transformations that took
                place during factor evaluation.
            encoder_state: The state of any stateful transformations that took
                place during encoding.
    """

    # Configuration attributes
    formula: Formula
    materializer: Optional[str] = None
    ensure_full_rank: bool = True
    na_action: NAAction = "drop"
    output: Optional[str] = None

    # State attributes
    structure: Optional[List[EncodedTermStructure]] = None
    transform_state: Dict = field(default_factory=dict)
    encoder_state: Dict = field(default_factory=dict)

    def __post_init__(self):
        self.__dict__["formula"] = Formula.from_spec(self.formula)

        if not self.formula._has_root or self.formula._has_structure:
            raise ValueError(
                "Nominated `Formula` instance has structure, which is not permitted when attaching to a `ModelSpec` instance."
            )

        # Materializer
        if (
            isinstance(self.materializer, FormulaMaterializer)
            or inspect.isclass(self.materializer)
            and issubclass(self.materializer, FormulaMaterializer)
        ):
            self.__dict__["materializer"] = self.materializer.REGISTER_NAME
        assert self.materializer is None or isinstance(
            self.materializer, str
        ), self.materializer

        self.__dict__["na_action"] = NAAction(self.na_action)

    # Derived features

    @cached_property
    def column_names(self) -> Sequence[str]:
        """
        The names associated with the columns of the generated model matrix.
        """
        return tuple(feature for row in self.structure for feature in row.columns)

    @property
    def feature_names(self) -> Sequence[str]:
        """
        A deprecated reference to `ModelSpec.column_names`. Will be removed in
        v1.0.0.
        """
        warnings.warn(
            "`ModelSpec.feature_names` is deprecated and will be removed in v1.0.0. Use `ModelSpec.column_names` instead.",
            DeprecationWarning,
        )
        return self.column_names

    @cached_property
    def column_indices(self) -> OrderedDict[str, int]:
        """
        An ordered mapping from column names to the column index in generated
        model matrices.
        """
        return OrderedDict([(name, i) for i, name in enumerate(self.column_names)])

    @property
    def feature_indices(self) -> Sequence[str]:
        """
        A deprecated reference to `ModelSpec.column_indices`. Will be removed in
        v1.0.0.
        """
        warnings.warn(
            "`ModelSpec.feature_indices` is deprecated and will be removed in v1.0.0. Use `ModelSpec.column_indices` instead.",
            DeprecationWarning,
        )
        return self.column_indices

    @property
    def terms(self) -> List[Term]:
        """
        The terms used to generate model matrices from this `ModelSpec`
        instance.
        """
        return self.formula.root

    @cached_property
    def term_indices(self) -> OrderedDict[Term, Tuple[int, ...]]:
        """
        An ordered mapping of `Term` instances to the generated column indices.

        Note: Since terms hash using their string representation, you can look
        up elements of this mapping using the string representation of the
        `Term`.
        """
        slices = OrderedDict()
        start = 0
        for row in self.structure:
            end = start + len(row[2])
            slices[row[0]] = tuple(range(start, end))
            start = end
        return slices

    @cached_property
    def term_slices(self) -> OrderedDict[Term, slice]:
        """
        An ordered mapping of `Term` instances to a slice that when used on
        the columns of the model matrix will subsample the model matrix down to
        those corresponding to each term.

        Note: Since terms hash using their string representation, you can look
        up elements of this mapping using the string representation of the
        `Term`.
        """
        return OrderedDict(
            {k: slice(v[0], v[-1] + 1) for k, v in self.term_indices.items()}
        )

    # Transforms

    def update(self, **kwargs):
        """
        Create a copy of this `ModelSpec` instance with the nominated attributes
        mutated.
        """
        return replace(self, **kwargs)

    def differentiate(self, *vars, use_sympy=False):
        """
        EXPERIMENTAL: Take the gradient of this model spec. When used a linear
        regression, evaluating a trained model on model matrices generated by
        this formula is equivalent to estimating the gradient of that fitted
        form with respect to `vars`.

        Args:
            vars: The variables with respect to which the gradient should be
                taken.
            use_sympy: Whether to use sympy to perform symbolic differentiation.

        Notes:
            This method is provisional and may be removed in any future major
            version.
        """
        return self.update(
            formula=self.formula.differentiate(*vars, use_sympy=use_sympy),
        )

    # Utility methods

    def get_model_matrix(
        self, data: Any, **kwargs
    ) -> Union[ModelMatrix, ModelMatrices]:
        """
        Build the model matrix (or matrices) realisation of this model spec for
        the nominated `data`.

        Args:
            data: The data for which to build the model matrices.
            context: An additional mapping object of names to make available in
                when evaluating formula term factors.
            kwargs: Additional materializer-specific arguments to pass on to the
                materializer's `.get_model_matrix` method.
        """
        if self.materializer is None:
            materializer = FormulaMaterializer.for_data(data)
        else:
            materializer = FormulaMaterializer.for_materializer(self.materializer)
        return materializer(data, **kwargs).get_model_matrix(self)

    def get_linear_constraints(self, spec: LinearConstraintSpec) -> LinearConstraints:
        """
        Construct a `LinearConstraints` instance from a specification based on
        the structure of the model matrices associated with this model spec.

        Args:
            spec: The specification from which to derive the constraints. Refer
                to `LinearConstraints.from_spec` for more details.
        """
        return LinearConstraints.from_spec(spec, variable_names=self.column_names)

    def get_slice(self, columns_identifier: Union[int, str, Term, slice]) -> slice:
        """
        Generate a `slice` instance corresponding to the columns associated with
        the nominated `columns_identifier`.

        Args:
            columns_identifier: The identifier for which the slice should be
                generated. Can be one of:
                    - an integer specifying a specific column index.
                    - a `Term` instance
                    - a string representation of a term
                    - a column name
        """
        if isinstance(columns_identifier, slice):
            return columns_identifier
        if isinstance(columns_identifier, int):
            return slice(columns_identifier, columns_identifier + 1)

        term_slices = self.term_slices
        if isinstance(columns_identifier, Term):
            if columns_identifier not in term_slices:
                raise ValueError(
                    f"Model matrices built using this spec do not include term: `{columns_identifier}`."
                )
            return term_slices[columns_identifier]
        if columns_identifier in term_slices:
            return term_slices[columns_identifier]

        column_indices = self.column_indices
        if columns_identifier in column_indices:
            idx = column_indices[columns_identifier]
            return slice(idx, idx + 1)

        raise ValueError(
            f"Model matrices built using this spec do not have any columns related to: `{repr(columns_identifier)}`."
        )

    # Only include dataclass fields when pickling.
    def __getstate__(self):
        return {
            k: v for k, v in self.__dict__.items() if k in self.__dataclass_fields__
        }


class ModelSpecs(Structured[ModelSpec]):
    """
    A `Structured[ModelSpec]` subclass that exposes some convenience methods
    that should be mapped onto all contained `ModelSpec` instances.
    """

    def _prepare_item(self, key: str, item: Any) -> Any:
        # Verify that all included items are `ModelSpec` instances.
        if not isinstance(item, ModelSpec):
            raise TypeError(
                "`ModelSpecs` instances expect all items to be instances of "
                f"`ModelSpec`. [Got: {repr(item)} of type {repr(type(item))} "
                f"for key {repr(key)}."
            )
        return item

    def get_model_matrix(self, data, **kwargs) -> ModelMatrices:
        """
        This method proxies the `ModelSpec.get_model_matrix(...)` API and allows
        it to be called on a structured set of `ModelSpec` instances. If all
        `ModelSpec.materializer` values are unset or the same, then they are
        jointly evaluated allowing re-use of the same cached across the specs.

        Args:
            data: The data for which model matrices should be generated.
            kwargs: Additional keyword arguments to pass on to
            `ModelSpec.get_model_matrix(...)` and thus to the materializer.
        """
        materializers = set(
            self._map(lambda model_spec: model_spec.materializer)._flatten()
        ).difference({None})
        if (
            len(materializers) > 1
        ):  # pragma: no cover; sentinel for when model specs have been manually constructed and are incompatible
            return self._map(
                lambda model_spec: model_spec.get_model_matrix(data, **kwargs)
            )
        if materializers:
            materializer = materializer = FormulaMaterializer.for_materializer(
                next(iter(materializers))
            )
        else:
            materializer = FormulaMaterializer.for_data(data)
        return materializer(data, **kwargs).get_model_matrix(self)

    def differentiate(self, *vars, use_sympy=False) -> ModelSpecs:
        """
        This method proxies the experimental `ModelSpec.differentiate(...)` API.
        See `ModelSpec.differentiate` for more details.
        """
        return self._map(
            lambda model_spec: model_spec.differentiate(*vars, use_sympy=use_sympy),
            as_type=ModelSpecs,
        )
