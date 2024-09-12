import dataclasses
import uuid
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional

import imap_data_access
import numpy as np
from spacepy.pycdf import CDF
from uncertainties import ufloat
from uncertainties.unumpy import uarray

from imap_processing.cdf.cdf_utils import write_cdf
from imap_processing.cdf.imap_attribute_manager import ImapAttributeManager
from imap_processing.constants import THIRTY_SECONDS_IN_NANOSECONDS, TEMP_CDF_FOLDER_PATH
from imap_processing.models import UpstreamDataDependency, DataProduct
from imap_processing.processor import Processor, download_dependency
from imap_processing.swapi.l3a.models import SwapiL3ProtonSolarWindData, SwapiL3AlphaSolarWindData
from imap_processing.swapi.l3a.science.calculate_alpha_solar_wind_speed import calculate_alpha_solar_wind_speed
from imap_processing.swapi.l3a.science.calculate_proton_solar_wind_speed import calculate_proton_solar_wind_speed
from imap_processing.swapi.l3a.science.calculate_proton_solar_wind_temperature_and_density import \
    TemperatureAndDensityCalibrationTable, calculate_proton_solar_wind_temperature_and_density
from imap_processing.swapi.l3a.utils import read_l2_swapi_data, chunk_l2_data

SWAPI_L2_DESCRIPTOR = "fake-menlo-5-sweeps"
TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR = "density-temperature-lut-text-not-cdf"


@dataclass
class SwapiL3ADependencies:
    data: CDF
    temperature_density_calibration_table: TemperatureAndDensityCalibrationTable

    @classmethod
    def fetch_dependencies(cls, dependencies: list[UpstreamDataDependency]):
        try:
            data_dependency = next(
                dep for dep in dependencies if dep.descriptor == SWAPI_L2_DESCRIPTOR)
            data_dependency_path = download_dependency(data_dependency)
        except StopIteration:
            raise ValueError(f"Missing {SWAPI_L2_DESCRIPTOR} dependency.")
        except ValueError as e:
            raise ValueError(f"Unexpected files found for SWAPI L3:"
                             f"{e}")

        try:
            calibration_table_dependency = next(
                dep for dep in dependencies if dep.descriptor == TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR)
            calibration_table_dependency = dataclasses.replace(calibration_table_dependency, start_date=None,
                                                               end_date=None)
            calibration_table_dependency_path = download_dependency(calibration_table_dependency)
        except StopIteration:
            raise ValueError(f"Missing {TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR} dependency.")
        except ValueError as e:
            raise ValueError(f"Unexpected files found for SWAPI L3:"
                             f"{e}")

        temperature_and_density_calibration_file = TemperatureAndDensityCalibrationTable.from_file(
            calibration_table_dependency_path)
        data_file = CDF(str(data_dependency_path))
        return cls(data_file, temperature_and_density_calibration_file)


class SwapiL3AProcessor(Processor):

    def process(self):
        dependencies = [dataclasses.replace(dep, start_date=self.start_date, end_date=self.end_date) for dep in
                        self.dependencies]
        dependencies = SwapiL3ADependencies.fetch_dependencies(dependencies)

        data = read_l2_swapi_data(dependencies.data)

        epochs = []

        proton_solar_wind_speeds = []
        proton_solar_wind_temperatures = []
        proton_solar_wind_density = []

        alpha_solar_wind_speeds = []

        for data_chunk in chunk_l2_data(data, 5):
            coincidence_count_rates_with_uncertainty = uarray(data_chunk.coincidence_count_rate,
                                                              data_chunk.coincidence_count_rate_uncertainty)
            proton_solar_wind_speed, a, phi, b = calculate_proton_solar_wind_speed(
                coincidence_count_rates_with_uncertainty,
                data_chunk.spin_angles, data_chunk.energy, data_chunk.epoch)
            proton_solar_wind_speeds.append(proton_solar_wind_speed)

            temperature, density = calculate_proton_solar_wind_temperature_and_density(
                dependencies.temperature_density_calibration_table,
                proton_solar_wind_speed,
                ufloat(0.01, 1.0),
                phi,
                coincidence_count_rates_with_uncertainty,
                data_chunk.energy)

            proton_solar_wind_temperatures.append(temperature)
            proton_solar_wind_density.append(density)

            epochs.append(data_chunk.epoch[0] + THIRTY_SECONDS_IN_NANOSECONDS)

            alpha_solar_wind_speeds.append(calculate_alpha_solar_wind_speed(
                coincidence_count_rates_with_uncertainty,
                data_chunk.energy
            ))

        proton_solar_wind_l3_data = SwapiL3ProtonSolarWindData(np.array(epochs), np.array(proton_solar_wind_speeds),
                                                               np.array(proton_solar_wind_temperatures),
                                                               np.array(proton_solar_wind_density))
        self.upload_data(proton_solar_wind_l3_data, "proton-sw")

        alpha_solar_wind_l3_data = SwapiL3AlphaSolarWindData(np.array(epochs), np.array(alpha_solar_wind_speeds))
        self.upload_data(alpha_solar_wind_l3_data, "alpha-sw")
