# Security

Report vulnerabilities privately through GitHub's private vulnerability reporting when enabled. If unavailable, ask the maintainer for a private reporting channel without posting exploit details, credentials, or recordings publicly.

The security boundary assumes the desktop account is trusted. A local account compromise can expose recordings, pairing sessions, and the local CA private key. Do not expose the listener through port forwarding or a public tunnel. Private addressing reduces accidental exposure but does not replace a firewall.

Pairing QR codes are short-lived bearer secrets. Installing a root certificate is a security-sensitive user action and is never automatic. Compare fingerprints through a trusted path. An HTTP certificate download cannot prove its own authenticity. Remove the certificate profile and full trust on the phone when decommissioning a desktop; deleting application files alone does not remove phone trust.

Release security verification is incomplete until the evidence gates pass. No production security audit is claimed.
