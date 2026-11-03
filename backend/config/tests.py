from unittest.mock import patch

from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.test import APITestCase


class HealthCheckTests(APITestCase):
    def test_health_check_reports_database_availability(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"status": "ok"})

    @patch("config.urls.connection.ensure_connection", side_effect=OperationalError)
    def test_health_check_returns_unavailable_when_database_is_down(self, _ensure_connection):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data, {"status": "unavailable"})
