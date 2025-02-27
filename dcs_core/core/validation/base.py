#  Copyright 2022-present, the Waterdip Labs Pvt. Ltd.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import datetime
import json
import sys
import traceback
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Union

from loguru import logger

from dcs_core.core.common.models.configuration import (
    DataSourceLanguageSupport,
    ValidationConfig,
)
from dcs_core.core.common.models.validation import (
    ConditionType,
    DeltaValidationInfo,
    ValidationFunction,
    ValidationInfo,
)
from dcs_core.core.datasource.manager import DataSource


class ValidationIdentity:
    @staticmethod
    def generate_identity(
        validation_function: ValidationFunction,
        validation_name: str,
        data_source_name: str = None,
        dataset_name: str = None,
        field_name: str = None,
    ) -> str:
        """
        Generate a unique identifier for a metric
        """

        identifiers = []

        if data_source_name is not None:
            identifiers.append(data_source_name)
        if dataset_name:
            identifiers.append(dataset_name)
        if field_name:
            identifiers.append(field_name)
        if validation_function:
            identifiers.append(validation_function.value)
        if validation_name:
            identifiers.append(validation_name)
        return ".".join([str(p) for p in identifiers])


class Validation(ABC):
    """
    Validation is a class that represents a validation that is generated by a data source.
    """

    def __init__(
        self,
        name: str,
        validation_config: ValidationConfig,
        data_source: DataSource,
        dataset_name: str,
        field_name: str = None,
        **kwargs,
    ):
        self.name = name
        self.validation_config = validation_config
        self.data_source = data_source
        self.dataset_name = dataset_name
        self.field_name = field_name

        self.query = validation_config.query

        self.threshold = validation_config.threshold
        self.where_filter = None
        self.values = None
        self.regex_pattern = validation_config.regex

        if validation_config.where:
            if data_source.language_support == DataSourceLanguageSupport.DSL_ES:
                self.where_filter = json.loads(validation_config.where)
            elif data_source.language_support == DataSourceLanguageSupport.SQL:
                self.where_filter = validation_config.where
        if validation_config.values:
            if data_source.language_support == DataSourceLanguageSupport.SQL:
                self.values = validation_config.values

    def get_validation_identity(self) -> str:
        return ValidationIdentity.generate_identity(
            validation_function=self.validation_config.get_validation_function,
            validation_name=self.name,
            data_source_name=self.data_source.data_source_name,
            dataset_name=self.dataset_name,
            field_name=self.field_name,
        )

    def _validate_threshold(self, metric_value) -> Tuple[bool, Optional[str]]:
        for operator, value in self.threshold.__dict__.items():
            if value is not None:
                if ConditionType.GTE == operator:
                    if metric_value < value:
                        return (
                            False,
                            f"Less than threshold value of {value}",
                        )
                elif ConditionType.LTE == operator:
                    if metric_value > value:
                        return (
                            False,
                            f"Greater than threshold value of {value}",
                        )
                elif ConditionType.GT == operator:
                    if metric_value <= value:
                        return (
                            False,
                            f"Less than or equal to threshold value of {value}",
                        )
                elif ConditionType.LT == operator:
                    if metric_value >= value:
                        return (
                            False,
                            f"Greater than or equal to threshold value of {value}",
                        )
                elif ConditionType.EQ == operator:
                    if metric_value != value:
                        return (
                            False,
                            f"Not equal to the value of {value}",
                        )
        return True, None

    @abstractmethod
    def _generate_metric_value(self, **kwargs) -> Union[float, int]:
        pass

    def get_validation_info(self, **kwargs) -> Union[ValidationInfo, None]:
        try:
            metric_value = self._generate_metric_value(**kwargs)
            tags = {
                "name": self.name,
            }

            value = ValidationInfo(
                name=self.name,
                identity=self.get_validation_identity(),
                data_source_name=self.data_source.data_source_name,
                dataset=self.dataset_name,
                validation_function=self.validation_config.get_validation_function,
                field=self.field_name,
                value=metric_value,
                timestamp=datetime.datetime.utcnow(),
                tags=tags,
            )
            if self.threshold is not None:
                value.is_valid, value.reason = self._validate_threshold(metric_value)

            return value
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            logger.error(f"Failed to generate metric {self.name}: {str(e)}")
            return None


class DeltaValidation(Validation, ABC):
    def __init__(
        self,
        name: str,
        validation_config: ValidationConfig,
        data_source: DataSource,
        dataset_name: str,
        reference_data_source: DataSource,
        reference_dataset_name: str,
        reference_field_name: str = None,
        **kwargs,
    ):
        super().__init__(name, validation_config, data_source, dataset_name, **kwargs)
        self.reference_data_source = reference_data_source
        self.reference_dataset_name = reference_dataset_name
        self.reference_field_name = reference_field_name

    @abstractmethod
    def _generate_reference_metric_value(self, **kwargs) -> Union[float, int]:
        pass

    def get_validation_info(self, **kwargs) -> Union[ValidationInfo, None]:
        try:
            metric_value = self._generate_metric_value(**kwargs)
            reference_metric_value = self._generate_reference_metric_value(**kwargs)
            delta_value = abs(metric_value - reference_metric_value)

            tags = {
                "name": self.name,
            }

            value = DeltaValidationInfo(
                name=self.name,
                identity=self.get_validation_identity(),
                data_source_name=self.data_source.data_source_name,
                dataset=self.dataset_name,
                validation_function=self.validation_config.get_validation_function,
                field=self.field_name,
                value=delta_value,
                source_value=metric_value,
                reference_value=reference_metric_value,
                reference_datasource_name=self.reference_data_source.data_source_name,
                reference_dataset=self.reference_dataset_name,
                timestamp=datetime.datetime.utcnow(),
                tags=tags,
            )
            if self.threshold is not None:
                value.is_valid, value.reason = self._validate_threshold(delta_value)

            return value
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            logger.error(f"Failed to generate metric {self.name}: {str(e)}")
            return None
