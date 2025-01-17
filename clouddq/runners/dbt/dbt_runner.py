# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path
from typing import Dict
from typing import Optional

import logging
import typing

from clouddq.integration.bigquery.bigquery_client import BigQueryClient
from clouddq.runners.dbt.dbt_connection_configs import DEFAULT_DBT_ENVIRONMENT_TARGET
from clouddq.runners.dbt.dbt_connection_configs import DbtConnectionConfig
from clouddq.runners.dbt.dbt_connection_configs import GcpDbtConnectionConfig
from clouddq.runners.dbt.dbt_utils import run_dbt
from clouddq.utils import get_template_file
from clouddq.utils import get_templates_path
from clouddq.utils import write_templated_file_to_path


logger = logging.getLogger(__name__)

DBT_TEMPLATED_FILE_LOCATIONS = {
    "profiles.yml": Path("dbt", "profiles.yml"),
    "dbt_project.yml": Path("dbt", "dbt_project.yml"),
    "main.sql": Path("dbt", "models", "data_quality_engine", "main.sql"),
    "dq_summary.sql": Path("dbt", "models", "data_quality_engine", "dq_summary.sql"),
}

HOURS_TO_EXPIRATION = "+hours_to_expiration: 24"

class DbtRunner:
    environment_target: str
    intermediate_table_expiration_hours: int
    num_threads: int
    connection_config: DbtConnectionConfig
    dbt_rule_binding_views_path: Path
    dbt_entity_summary_path: Path

    def __init__(
        self,
        gcp_project_id: str,
        gcp_bq_dataset_id: str,
        environment_target: Optional[str],
        gcp_region_id: Optional[str],
        gcp_service_account_key_path: Optional[Path],
        gcp_impersonation_credentials: Optional[str],
        intermediate_table_expiration_hours: int,
        num_threads: int,
        bigquery_client: Optional[BigQueryClient] = None,
        create_paths_if_not_exists: bool = True,
    ):
        # Prepare local dbt environment
        self.dbt_path = self._resolve_dbt_path(
            create_paths_if_not_exists=create_paths_if_not_exists,
            write_log=True,
        )
        self._prepare_dbt_project_path(
            intermediate_table_expiration_hours=intermediate_table_expiration_hours
        )
        self._prepare_dbt_main_path()
        self._prepare_rule_binding_view_path(write_log=True)
        self._prepare_entity_summary_path(write_log=True)
        self.num_threads = num_threads
        # Prepare connection configurations
        self._resolve_connection_configs(
            gcp_project_id=gcp_project_id,
            gcp_bq_dataset_id=gcp_bq_dataset_id,
            environment_target=environment_target,
            bigquery_client=bigquery_client,
            gcp_region_id=gcp_region_id,
            gcp_service_account_key_path=gcp_service_account_key_path,
            gcp_impersonation_credentials=gcp_impersonation_credentials,
            num_threads=num_threads,
        )
        logger.debug(f"Using 'dbt_profiles_dir': {self.dbt_profiles_dir}")

    def run(
        self, configs: Dict, debug: bool = False, dry_run: bool = False
    ) -> None:
        logger.debug(f"Running dbt in path: {self.dbt_path}")
        if debug:
            self.test_dbt_connection()
        run_dbt(
            dbt_path=self.dbt_path,
            dbt_profile_dir=self.dbt_profiles_dir,
            configs=configs,
            environment=self.environment_target,
            debug=False,
            dry_run=dry_run,
        )

    def test_dbt_connection(self):
        run_dbt(
            dbt_path=self.dbt_path,
            dbt_profile_dir=self.dbt_profiles_dir,
            environment=self.environment_target,
            debug=True,
            dry_run=True,
        )

    def get_dbt_path(self) -> Path:
        self._resolve_dbt_path(self.dbt_path)
        return Path(self.dbt_path)

    def get_rule_binding_view_path(self) -> Path:
        self._prepare_rule_binding_view_path()
        return Path(self.dbt_rule_binding_views_path)

    def get_entity_summary_path(self) -> Path:
        self._prepare_entity_summary_path()
        return Path(self.dbt_entity_summary_path)

    def get_dbt_profiles_dir_and_environment_target(self,
                                                    gcp_project_id: str,
                                                    gcp_bq_dataset_id: str,
                                                    gcp_region_id: Optional[str] = None,
                                                    bigquery_client: Optional[BigQueryClient] = None,
                                                    ) -> typing.Tuple:
        self._resolve_connection_configs(
            gcp_project_id=gcp_project_id,
            gcp_bq_dataset_id=gcp_bq_dataset_id,
            gcp_region_id=gcp_region_id,
            bigquery_client=bigquery_client,
            environment_target=self.environment_target,
            num_threads=self.num_threads,
        )

        return (
            Path(self.dbt_profiles_dir),
            self.environment_target
        )

    def _resolve_connection_configs(
        self,
        gcp_project_id: str,
        gcp_bq_dataset_id: str,
        environment_target: Optional[str],
        num_threads: int,
        bigquery_client: Optional[BigQueryClient] = None,
        gcp_region_id: Optional[str] = None,
        gcp_service_account_key_path: Optional[Path] = None,
        gcp_impersonation_credentials: Optional[str] = None,
    ) -> None:
        # create GcpDbtConnectionConfig
        connection_config = GcpDbtConnectionConfig(
            gcp_project_id=gcp_project_id,
            gcp_bq_dataset_id=gcp_bq_dataset_id,
            threads=num_threads,
            bigquery_client=bigquery_client,
            gcp_region_id=gcp_region_id,
            gcp_service_account_key_path=gcp_service_account_key_path,
            gcp_impersonation_credentials=gcp_impersonation_credentials,
        )
        self.connection_config = connection_config
        self.dbt_profiles_dir = Path(self.dbt_path)
        logger.debug(
            "Using dbt profiles.yml path: {self.dbt_profiles_dir}",
        )
        if environment_target:
            logger.debug(f"Using `environment_target`: {environment_target}")
            self.environment_target = environment_target
        else:
            self.environment_target = DEFAULT_DBT_ENVIRONMENT_TARGET

        self.connection_config.to_dbt_profiles_yml(
            target_directory=self.dbt_profiles_dir,
            environment_target=self.environment_target,
        )

    def _resolve_dbt_path(
        self,
        create_paths_if_not_exists: bool = False,
        write_log: bool = False,
    ) -> Path:
        logger.debug(f"Current working directory: {Path().cwd()}")
        dbt_path = Path().cwd().joinpath("dbt").absolute()
        logger.debug(f"Defaulting to use 'dbt' directory in current working directory at: {dbt_path}")
        if not dbt_path.is_dir():
            if create_paths_if_not_exists:
                logger.debug(f"Creating a new dbt directory at 'dbt_path': {dbt_path}")
                dbt_path.mkdir(parents=True, exist_ok=True)
            else:
                raise ValueError(f"Provided 'dbt_path' does not exists: {dbt_path}")
        if write_log:
            logger.debug(f"Using 'dbt_path': {dbt_path}")
        return dbt_path

    def _prepare_dbt_project_path(self,
                                  intermediate_table_expiration_hours: int) -> None:

        dbt_project_path = self.dbt_path.absolute().joinpath("dbt_project.yml")
        if not dbt_project_path.is_file():
            logger.debug(
                f"Cannot find `dbt_project.yml` in path: {dbt_project_path} \n"
                f"Writing templated file to: {dbt_project_path}/dbt_project.yml"
            )
        else:
            logger.debug(f"Using 'dbt_project_path': {dbt_project_path}")

        logger.debug(
            f"Started setting Intermediate table expiration hours "
            f"value to {intermediate_table_expiration_hours}"
        )
        template_text = get_template_file(
            DBT_TEMPLATED_FILE_LOCATIONS.get(dbt_project_path.name)
        )
        if HOURS_TO_EXPIRATION in template_text:
            template_text = template_text.replace(
                HOURS_TO_EXPIRATION,
                f"{HOURS_TO_EXPIRATION.split(':')[0]}: {intermediate_table_expiration_hours}",
            )
            logger.debug(f"Writing templated file "
                         f"{get_templates_path(dbt_project_path.name)} to "
                         f"{dbt_project_path}")
            dbt_project_path.write_text(template_text)
            logger.debug(
                f"Intermediate table expiration value is set to"
                f"{intermediate_table_expiration_hours} hours successfully."
            )
        else:
            raise ValueError(f"Failed to set intermediate table expiration "
                             f"value to {intermediate_table_expiration_hours} hours")

    def _prepare_dbt_main_path(self) -> None:
        assert self.dbt_path.is_dir()
        dbt_main_path = self.dbt_path / "models" / "data_quality_engine"
        dbt_main_path.mkdir(parents=True, exist_ok=True)
        write_templated_file_to_path(
            dbt_main_path.joinpath("main.sql"), DBT_TEMPLATED_FILE_LOCATIONS
        )
        write_templated_file_to_path(
            dbt_main_path.joinpath("dq_summary.sql"), DBT_TEMPLATED_FILE_LOCATIONS
        )

    def _prepare_rule_binding_view_path(self, write_log: bool = False) -> None:
        assert self.dbt_path.is_dir()
        self.dbt_rule_binding_views_path = (
            self.dbt_path / "models" / "rule_binding_views"
        )
        self.dbt_rule_binding_views_path.mkdir(parents=True, exist_ok=True)
        if write_log:
            logger.debug(
                "Using rule_binding_views path: "
                f"{self.dbt_rule_binding_views_path.absolute()}/",
            )

    def _prepare_entity_summary_path(self, write_log: bool = False) -> None:
        assert self.dbt_path.is_dir()
        self.dbt_entity_summary_path = (
            self.dbt_path / "models" / "entity_dq_statistics"
        )
        self.dbt_entity_summary_path.mkdir(parents=True, exist_ok=True)
        if write_log:
            logger.debug(
                "Using entity_dq_statistics path: "
                f"{self.dbt_entity_summary_path.absolute()}/",
            )
