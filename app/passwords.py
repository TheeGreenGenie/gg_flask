"""Shared constant for timing-safe password checks."""

# Verified instead of a real hash when a username does not exist, so an unknown account takes as
# long as a wrong password and sign-in timing cannot reveal which usernames exist. It hashes a
# random value that was never kept. It is fixed rather than computed at startup because the host
# restarts the app after idle periods and three hashes cost ~0.2 s of every wake-up.
# tests/test_security.py fails if Argon2's default settings change and this needs regenerating.
DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$qL9X62KREGdmTSkuHWv4mA"
    "$Ri9JgpHx4YkxcoTfH1WblIkTIEBhL7R5ugZrfzYtP+E"
)
