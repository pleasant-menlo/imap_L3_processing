from datetime import datetime, date
from unittest import TestCase
from unittest.mock import patch, call

import numpy as np

from imap_processing.constants import TEMP_CDF_FOLDER_PATH
from imap_processing.models import UpstreamDataDependency
from imap_processing.swapi.l3a.models import SwapiL3AlphaSolarWindData
from imap_processing.utils import upload_data, format_time, download_dependency


class TestUtils(TestCase):
    @patch("imap_processing.utils.ImapAttributeManager")
    @patch("imap_processing.utils.date")
    @patch("imap_processing.utils.uuid.uuid4")
    @patch("imap_processing.utils.write_cdf")
    @patch("imap_processing.utils.imap_data_access.upload")
    def test_upload_data(self, mock_upload, mock_write_cdf, mock_uuid, mock_today, _):
        mock_today.today.return_value = date(2024, 9, 16)
        mock_uuid.return_value = 444

        input_metadata = UpstreamDataDependency("swapi", "l2", datetime(2024, 9, 17), datetime(2024, 9, 18), "v2", "descriptor")
        epoch = np.array([1, 2, 3])
        alpha_sw_speed = np.array([4, 5, 6])

        data_product = SwapiL3AlphaSolarWindData(input_metadata=input_metadata, epoch=epoch, alpha_sw_speed=alpha_sw_speed)
        upload_data(data_product)

        mock_write_cdf.assert_called_once()
        actual_file_path = mock_write_cdf.call_args.args[0]
        actual_data = mock_write_cdf.call_args.args[1]
        actual_attribute_manager = mock_write_cdf.call_args.args[2]

        expected_file_path = f"{TEMP_CDF_FOLDER_PATH}/imap_swapi_l2_descriptor-fake-menlo-444_20240917_v2.cdf"
        self.assertEqual(expected_file_path, actual_file_path)
        self.assertIs(data_product, actual_data)

        actual_attribute_manager.add_global_attribute.assert_has_calls([
            call("Data_version", "v2"),
            call("Generation_date", "20240916"),
            call("Logical_source", "imap_swapi_l2_descriptor"),
            call("Logical_file_id", "imap_swapi_l2_descriptor-fake-menlo-444_20240917_v2")
        ])

        actual_attribute_manager.add_instrument_attrs.assert_called_with(
            "swapi", "l2"
        )

        mock_upload.assert_called_once_with(expected_file_path)

    def test_format_time(self):
        time = datetime(2024, 7, 9)
        actual_time = format_time(time)
        self.assertEqual("20240709", actual_time)

        actual_time = format_time(None)
        self.assertEqual(None, actual_time)

    @patch('imap_processing.utils.imap_data_access')
    def test_download_dependency(self, mock_data_access):
        dependency = UpstreamDataDependency("swapi", "l2", datetime(2024, 9, 17), datetime(2024, 9, 18), "v2", "descriptor")
        query_dictionary = [{'file_path': "imap_swapi_l2_descriptor-fake-menlo-444_20240917_v2.cdf",
                            'second_entry': '12345'}]
        mock_data_access.query.return_value = query_dictionary

        path = download_dependency(dependency)

        mock_data_access.query.assert_called_once_with(instrument=dependency.instrument,
                                                       data_level=dependency.data_level,
                                                       descriptor=dependency.descriptor,
                                                       start_date="20240917",
                                                       end_date="20240918",
                                                       version='latest')
        mock_data_access.download.asser_called_once_with("imap_swapi_l2_descriptor-fake-menlo-444_20240917_v2.cdf")

        self.assertIs(path, mock_data_access.download.return_value)

    @patch('imap_processing.utils.imap_data_access')
    def test_download_dependency_throws_value_error_if_more_than_one_file_returned(self, mock_data_access):
        dependency = UpstreamDataDependency("swapi", "l2", datetime(2024, 9, 17), datetime(2024, 9, 18), "v2",
                                            "descriptor")
        query_dictionary = [{'file_path': "imap_swapi_l2_descriptor-fake-menlo-444_20240917_v2.cdf",
                             'second_entry': '12345'}, {"file_path": "extra_value"}]
        mock_data_access.query.return_value = query_dictionary

        with self.assertRaises(Exception) as cm:
            download_dependency(dependency)

        self.assertEqual("['imap_swapi_l2_descriptor-fake-menlo-444_20240917_v2.cdf', 'extra_value']. Expected only one file to download.", str(cm.exception))