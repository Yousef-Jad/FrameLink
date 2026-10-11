# Contributing

Use a branch and keep changes focused. Run `python -m ruff check src tests tools` and `python -m pytest -q`. Media changes should include a decodable output test, timing assertions, and failure-path coverage. Security changes should include malformed and unauthorized request tests.

Platform capture changes require evidence on the affected OS. Record the OS, backend, display/camera, driver, resolution, requested and observed rate, duration, and test result. Do not treat a mock or a cross-platform import as proof of hardware compatibility.

Do not commit private recordings, certificate keys, pairing URLs, credentials, or personal device details. Use synthetic fixtures for reproducible automated tests and label them accordingly. Keep public documentation aligned with executed behavior and retain upstream license notices.
