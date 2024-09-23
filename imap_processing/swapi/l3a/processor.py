import dataclasses
from dataclasses import dataclass
import numpy as np
from spacepy.pycdf import CDF
from uncertainties import ufloat
from uncertainties.unumpy import uarray

from imap_processing.constants import THIRTY_SECONDS_IN_NANOSECONDS
from imap_processing.models import UpstreamDataDependency
from imap_processing.processor import Processor
from imap_processing.swapi.l3a.models import SwapiL3ProtonSolarWindData, SwapiL3AlphaSolarWindData
from imap_processing.swapi.l3a.science.calculate_alpha_solar_wind_speed import calculate_alpha_solar_wind_speed
from imap_processing.swapi.l3a.science.calculate_alpha_solar_wind_temperature_and_density import \
    AlphaTemperatureDensityCalibrationTable, calculate_alpha_solar_wind_temperature_and_density_for_combined_sweeps
from imap_processing.swapi.l3a.science.calculate_proton_solar_wind_clock_and_deflection_angles import \
    ClockAngleCalibrationTable, calculate_deflection_angle, calculate_clock_angle
from imap_processing.swapi.l3a.science.calculate_proton_solar_wind_speed import calculate_proton_solar_wind_speed
from imap_processing.swapi.l3a.science.calculate_proton_solar_wind_temperature_and_density import \
    ProtonTemperatureAndDensityCalibrationTable, calculate_proton_solar_wind_temperature_and_density
from imap_processing.swapi.l3a.utils import read_l2_swapi_data, chunk_l2_data
from imap_processing.utils import download_dependency, upload_data

SWAPI_L2_DESCRIPTOR = "sci"
PROTON_TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR = "density-temperature-lut-text-not-cdf"
ALPHA_TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR = "alpha-density-temperature-lut-text-not-cdf"
CLOCK_ANGLE_AND_FLOW_DEFLECTION_LOOKUP_TABLE_DESCRIPTOR = "clock-angle-and-flow-deflection-lut-text-not-cdf"


@dataclass
class SwapiL3ADependencies:
    data: CDF
    proton_temperature_density_calibration_table: ProtonTemperatureAndDensityCalibrationTable
    alpha_temperature_density_calibration_table: AlphaTemperatureDensityCalibrationTable
    clock_angle_and_flow_deflection_calibration_table: ClockAngleCalibrationTable

    @classmethod
    def fetch_dependencies(cls, dependencies: list[UpstreamDataDependency]):
        try:
            data_dependency = next(
                dep for dep in dependencies if dep.descriptor == SWAPI_L2_DESCRIPTOR)
        except StopIteration:
            raise ValueError(f"Missing {SWAPI_L2_DESCRIPTOR} dependency.")

        proton_density_and_temperature_calibration_file = UpstreamDataDependency("swapi", "l2", None, None,
                                                                                 "latest",
                                                                                 PROTON_TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR)
        alpha_density_and_temperature_calibration_file = UpstreamDataDependency("swapi", "l2", None, None,
                                                                                "latest",
                                                                                ALPHA_TEMPERATURE_DENSITY_LOOKUP_TABLE_DESCRIPTOR)

        clock_angle_and_deflection_calibration_table_dependency = UpstreamDataDependency("swapi", "l2", None, None,
                                                                                         "latest",
                                                                                         CLOCK_ANGLE_AND_FLOW_DEFLECTION_LOOKUP_TABLE_DESCRIPTOR)
        try:
            data_dependency_path = download_dependency(data_dependency)
            proton_density_and_temperature_calibration_file_path = download_dependency(
                proton_density_and_temperature_calibration_file)
            alpha_density_and_temperature_calibration_file_path = download_dependency(
                alpha_density_and_temperature_calibration_file)
            clock_and_deflection_file_path = download_dependency(
                clock_angle_and_deflection_calibration_table_dependency)
        except ValueError as e:
            raise ValueError(f"Unexpected files found for SWAPI L3:"
                             f"{e}")

        data_file = CDF(str(data_dependency_path))
        proton_temperature_and_density_calibration_file = ProtonTemperatureAndDensityCalibrationTable.from_file(
            proton_density_and_temperature_calibration_file_path)
        alpha_density_and_temperature_calibration_file = AlphaTemperatureDensityCalibrationTable.from_file(
            alpha_density_and_temperature_calibration_file_path)
        clock_angle_calibration_file = ClockAngleCalibrationTable.from_file(clock_and_deflection_file_path)

        return cls(data_file, proton_temperature_and_density_calibration_file,
                   alpha_density_and_temperature_calibration_file, clock_angle_calibration_file)


class SwapiL3AProcessor(Processor):

    def process(self):
        dependencies = [
            dataclasses.replace(dep, start_date=self.input_metadata.start_date, end_date=self.input_metadata.end_date)
            for dep in
            self.dependencies]
        dependencies = SwapiL3ADependencies.fetch_dependencies(dependencies)

        data = read_l2_swapi_data(dependencies.data)

        epochs = []

        proton_solar_wind_speeds = []
        proton_solar_wind_temperatures = []
        proton_solar_wind_density = []
        proton_solar_wind_clock_angles = []
        proton_solar_wind_deflection_angles = []

        alpha_solar_wind_speeds = []
        alpha_solar_wind_densities = []
        alpha_solar_wind_temperatures = []

        for data_chunk in chunk_l2_data(data, 5):
            coincidence_count_rates_with_uncertainty = uarray(data_chunk.coincidence_count_rate,
                                                              data_chunk.coincidence_count_rate_uncertainty)
            proton_solar_wind_speed, a, phi, b = calculate_proton_solar_wind_speed(
                coincidence_count_rates_with_uncertainty,
                data_chunk.spin_angles, data_chunk.energy, data_chunk.epoch)
            proton_solar_wind_speeds.append(proton_solar_wind_speed)

            proton_temperature, proton_density = calculate_proton_solar_wind_temperature_and_density(
                dependencies.proton_temperature_density_calibration_table,
                proton_solar_wind_speed,
                ufloat(0.01, 1.0),
                phi,
                coincidence_count_rates_with_uncertainty,
                data_chunk.energy)

            clock_angle = calculate_clock_angle(dependencies.clock_angle_and_flow_deflection_calibration_table,
                                                proton_solar_wind_speed, a, phi, b)

            deflection_angle = calculate_deflection_angle(
                dependencies.clock_angle_and_flow_deflection_calibration_table,
                proton_solar_wind_speed, a, phi, b)

            proton_solar_wind_temperatures.append(proton_temperature)
            proton_solar_wind_density.append(proton_density)
            proton_solar_wind_clock_angles.append(clock_angle)
            proton_solar_wind_deflection_angles.append(deflection_angle)

            epochs.append(data_chunk.epoch[0] + THIRTY_SECONDS_IN_NANOSECONDS)

            alpha_solar_wind_speed = calculate_alpha_solar_wind_speed(coincidence_count_rates_with_uncertainty,
                                                                      data_chunk.energy)
            alpha_solar_wind_speeds.append(alpha_solar_wind_speed)

            alpha_temperature, alpha_density = calculate_alpha_solar_wind_temperature_and_density_for_combined_sweeps(
                dependencies.alpha_temperature_density_calibration_table, alpha_solar_wind_speed,
                coincidence_count_rates_with_uncertainty,
                data_chunk.energy)

            alpha_solar_wind_densities.append(alpha_density)
            alpha_solar_wind_temperatures.append(alpha_temperature)

        proton_solar_wind_speed_metadata = self.input_metadata.to_upstream_data_dependency("proton-sw")

        proton_solar_wind_l3_data = SwapiL3ProtonSolarWindData(proton_solar_wind_speed_metadata, np.array(epochs),
                                                               np.array(proton_solar_wind_speeds),
                                                               np.array(proton_solar_wind_temperatures),
                                                               np.array(proton_solar_wind_density),
                                                               np.array(proton_solar_wind_clock_angles),
                                                               np.array(proton_solar_wind_deflection_angles))
        upload_data(proton_solar_wind_l3_data)

        alpha_solar_wind_speed_metadata = self.input_metadata.to_upstream_data_dependency("alpha-sw")
        alpha_solar_wind_l3_data = SwapiL3AlphaSolarWindData(alpha_solar_wind_speed_metadata, np.array(epochs),
                                                             np.array(alpha_solar_wind_speeds),
                                                             np.array(alpha_solar_wind_temperatures),
                                                             np.array(alpha_solar_wind_densities))
        upload_data(alpha_solar_wind_l3_data)
