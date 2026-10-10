#!/bin/sh
cd "$(dirname "$0")"
python3 -m unittest tests.test_platform_adapter tests.test_platform_registry tests.test_fleet_platforms tests.test_platform_integration -v
