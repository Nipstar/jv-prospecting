"""Discovery subpackage — Prospector v2 Phase 2.

New businesses are found here via Google Places text search
(discovery/places.py), as distinct from prospector/places_client.py, which
is the low-level Google Places API HTTP client (reused/extended, not
duplicated, by discovery/places.py) and also still backs the legacy
`prospector run` ad-hoc-scoring pipeline.
"""
