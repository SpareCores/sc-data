## v0.4.1 (Feb 26, 2026)

Use versioned path for the collected data to support different versions of the
`sparecores-crawler` package.

## v0.4.0 (Feb 26, 2026)

‼ Breaking changes:

- New file format.
- New location and URL path of the collected data.

In previous versions of the `sparecores-data` package, the data collection
process was run by the public GitHub Actions workflow of its own GitHub
repository, which was not optimal:

- It mixed software and data in the same repository and Python package,
  resulting in confusing licensing terms.
- We shipped a stripped down version of the data with the package, so it can be
  used self-contained -- often resulted in unexpected behavior when the client
  expected full data.
- Distribution file became very large over the past years (500MB+ uncompressed,
  ~45MB compressed with bzip2), and the S3 hosting was slow on single threaded
  download speeds, also becoming expensive due to bandwidth costs.

To mitigate these issues, the data collection process was moved to a separate
GitHub repository at <https://github.com/SpareCores/sc-data-dumps>, and the data
is now distributed in lzma-compressed SQLite database dump format. This reduced
the file size to ~15MB (compressed), and the file is now served by a global CDN,
making it much faster to download and use.

Note that this changelog file was cleaned up from the thousands of automated
updates committed by the GitHub Actions workflow in the past years, so it
currently only lists the software-related important changes.

For a full list of previous changes removed from this changelog, see the original version at
<https://github.com/SpareCores/sc-data/blob/0fdae6d0c0a73ffb258667b4e9301e6563ab6a37/CHANGELOG.md>

## v0.3.3 (Jan 7, 2026)

Bump version to sync with the new `sparecores-crawler` v0.3.3 release.

## v0.3.1 (Oct 25, 2024)

Bump version to sync with the new `sparecores-crawler` v0.3.1 release.

Minor schema change: `server.storages` array items can include `description`.

## v0.3.0 (Aug 20, 2024)

Bump version to sync with the new `sparecores-crawler` v0.3.0 release.
No schema changes expected.

## v0.2.1 (June 4, 2024)

Updated schema of the collected data via `sparecores-crawler` v0.2.1.

## v0.2.0 (June 4, 2024)

Updated schema of the collected data via `sparecores-crawler` v0.2.0.

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
