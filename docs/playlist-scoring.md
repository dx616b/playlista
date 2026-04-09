# Playlist Scoring (v2)

Scoring is weighted and explainable per track:

- BPM component: 35%
- Energy component: 25%
- Danceability component: 15%
- Timbre component: 10%
- Harmonic component: 15%

Additional sequence logic combines:

- energy-curve fit (`warmup`, `peak`, `cooldown`, `flat`)
- pairwise transition scoring between adjacent tracks
- Camelot compatibility bonus for harmonic mixing
- BPM and energy continuity penalties on transitions
- key-confidence weighting and onset continuity for transition stability
- genre continuity weighting from mutagen tags (soft preference, not hard lock)
- stratified candidate pool (top-score + bridge + artist-diverse slices)
- global artist/album caps to avoid over-representation
- optional feedback hooks (`track_feedback`, `artist_feedback`) for personalized biasing

Each selected track stores reason codes:

- `bpm_component`
- `energy_component`
- `danceability_component`
- `timbre_component`
- `harmonic_component`
- `energy_curve_target`
- `energy_curve_deviation`
- `transition_harmonic_bonus`
- `transition_genre_score`
- `transition_bpm_delta`
- `transition_energy_delta`
- `transition_score`
- `transition_prev_camelot`
- `transition_cur_camelot`
- `transition_prev_genre`
- `transition_cur_genre`

## Quality Metrics Endpoint

`GET /playlists/{id}/quality` returns:

- `mean_adjacent_bpm_delta`
- `harmonic_compatibility_rate`
- `mean_adjacent_energy_delta`
- `mean_adjacent_timbre_delta`
- `adjacent_artist_repeat_rate`
- `artist_diversity_ratio`
- `genre_coherence_rate`
