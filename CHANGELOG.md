## v0.1.0 (March 25, 2024)

Initial PyPI release of `sparecores-data`:

- Continuous GitHub Actions infrastructure collecting Spare Cores data
  and making it available in a public repository.
- Helper functions for applications making use of the Spare Cores data
  to automatically download and make available the most recently
  updated SQLite file from the public repository.

Supported vendors:

- Amazon Web Services

Supported records:

- country
- compliance_framework
- vendor
- vendor_compliance_link
- datacenter
- zone
- server
- server_price
- storage
- storage_price
- traffic_price
- ipv4_price
