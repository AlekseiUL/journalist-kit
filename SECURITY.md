# Security Policy

## Supported state

This repository is an experimental private beta. The installed skill is offline and standard-library-only; it does not add network access, credentials, telemetry, background services, or publication rights.

## Reporting

Report a vulnerability privately to the repository owner through GitHub's private vulnerability reporting if enabled, or through an already trusted private channel. Do not place credentials, private source material, personal voice samples, local absolute paths, or production configuration in an issue.

Include the affected commit, exact command or input shape, observed result, expected boundary, and a minimal synthetic reproduction. Do not test against live profiles, accounts, publishing channels, or private user data.

## Release boundary

A clean package validator or secret-pattern scan is not proof that all sensitive material is absent. Before public release, inspect the working tree, reachable git history, and the exact distributed archive; resolve the pending license; run the release validator and tests from a clean checkout.
