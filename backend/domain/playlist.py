from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import Playlist, PlaylistTrack, Track, TrackFeaturesNorm


@dataclass
class RankedTrack:
    track: Track
    features: TrackFeaturesNorm
    score: float
    reason: dict


CAM_MAJOR = {
    "C": "8B",
    "G": "9B",
    "D": "10B",
    "A": "11B",
    "E": "12B",
    "B": "1B",
    "F#": "2B",
    "Gb": "2B",
    "Db": "3B",
    "C#": "3B",
    "Ab": "4B",
    "G#": "4B",
    "Eb": "5B",
    "D#": "5B",
    "Bb": "6B",
    "A#": "6B",
    "F": "7B",
}

CAM_MINOR = {
    "A": "8A",
    "E": "9A",
    "B": "10A",
    "F#": "11A",
    "Gb": "11A",
    "C#": "12A",
    "Db": "12A",
    "G#": "1A",
    "Ab": "1A",
    "D#": "2A",
    "Eb": "2A",
    "A#": "3A",
    "Bb": "3A",
    "F": "4A",
    "C": "5A",
    "G": "6A",
    "D": "7A",
}


def _normalize_genre(genre: str | None) -> str | None:
    if not genre:
        return None
    # Keep first tag when file uses "Rock;Alternative" style.
    primary = genre.split(";")[0].split("/")[0].strip().lower()
    return primary or None


def _genre_transition_score(genre_a: str | None, genre_b: str | None) -> float:
    g1 = _normalize_genre(genre_a)
    g2 = _normalize_genre(genre_b)
    if not g1 or not g2:
        return 0.5
    if g1 == g2:
        return 1.0
    # Soft compatibility for closely related labels.
    if g1 in g2 or g2 in g1:
        return 0.85
    return 0.15


def _bpm_score(bpm: float | None, min_bpm: float | None, max_bpm: float | None) -> float:
    if bpm is None:
        return 0.2
    if min_bpm is not None and bpm < min_bpm:
        return 0.0
    if max_bpm is not None and bpm > max_bpm:
        return 0.0
    return 1.0


def _energy_target(curve: str, position: int, total: int) -> float:
    progress = position / max(1, total - 1)
    if curve == "warmup":
        return progress
    if curve == "cooldown":
        return 1.0 - progress
    if curve == "peak":
        return 1.0 - abs(progress - 0.5) * 2.0
    return 0.5


def _camelot_key(key: str | None, scale: str | None) -> str | None:
    if not key or not scale:
        return None
    if scale.lower().startswith("maj"):
        return CAM_MAJOR.get(key)
    return CAM_MINOR.get(key)


def _camelot_compatible(cam_a: str | None, cam_b: str | None) -> bool:
    if not cam_a or not cam_b:
        return False
    if cam_a == cam_b:
        return True
    num_a, mode_a = int(cam_a[:-1]), cam_a[-1]
    num_b, mode_b = int(cam_b[:-1]), cam_b[-1]
    if mode_a == mode_b:
        if num_b in {(num_a % 12) + 1, ((num_a - 2) % 12) + 1}:
            return True
    if num_a == num_b and mode_a != mode_b:
        return True
    return False


def _camelot_harmonic_score(cam_a: str | None, cam_b: str | None) -> float:
    if not cam_a or not cam_b:
        return 0.0
    if cam_a == cam_b:
        return 1.0
    num_a, mode_a = int(cam_a[:-1]), cam_a[-1]
    num_b, mode_b = int(cam_b[:-1]), cam_b[-1]
    clockwise = (num_b - num_a) % 12
    distance = min(clockwise, (12 - clockwise) % 12)
    if mode_a == mode_b:
        if distance == 1:
            return 0.9
        if distance == 2:
            return 0.65
        return max(0.0, 0.45 - (distance * 0.05))
    if num_a == num_b:
        return 0.85
    if distance == 1:
        return 0.6
    return max(0.0, 0.35 - (distance * 0.04))


def _pair_transition_score(
    prev: RankedTrack | None,
    cur: RankedTrack,
    genre_weight: float = 0.12,
) -> tuple[float, dict]:
    if prev is None:
        return 0.0, {"transition_base": "seed"}
    bpm_prev = prev.features.bpm or 0.0
    bpm_cur = cur.features.bpm or 0.0
    bpm_delta = abs(bpm_prev - bpm_cur)
    bpm_penalty = min(1.0, bpm_delta / 20.0)

    e_prev = prev.features.energy or 0.0
    e_cur = cur.features.energy or 0.0
    energy_delta = abs(e_prev - e_cur)
    energy_penalty = min(1.0, energy_delta / 0.5)
    flux_delta = abs((prev.features.spectral_flux or 0.0) - (cur.features.spectral_flux or 0.0))
    flux_penalty = min(1.0, flux_delta / 0.5)
    dyn_delta = abs((prev.features.dynamic_complexity or 0.0) - (cur.features.dynamic_complexity or 0.0))
    dyn_penalty = min(1.0, dyn_delta / 0.5)

    c_prev = _camelot_key(prev.features.key, prev.features.scale)
    c_cur = _camelot_key(cur.features.key, cur.features.scale)
    harmonic_bonus = _camelot_harmonic_score(c_prev, c_cur)
    key_confidence = ((cur.features.key_strength or 0.0) + (prev.features.key_strength or 0.0)) / 2.0
    genre_score = _genre_transition_score(prev.track.genre, cur.track.genre)

    transition_score = (
        harmonic_bonus * 0.20
        + (1.0 - bpm_penalty) * 0.22
        + (1.0 - energy_penalty) * 0.16
        + (1.0 - flux_penalty) * 0.08
        + (1.0 - dyn_penalty) * 0.06
        + key_confidence * 0.10
        + genre_score * genre_weight
        + (1.0 - abs((cur.features.onset_rate or 0.0) - (prev.features.onset_rate or 0.0))) * 0.08
    )
    reason = {
        "transition_harmonic_bonus": round(harmonic_bonus, 4),
        "transition_genre_score": round(genre_score, 4),
        "transition_prev_genre": _normalize_genre(prev.track.genre),
        "transition_cur_genre": _normalize_genre(cur.track.genre),
        "transition_bpm_delta": round(bpm_delta, 4),
        "transition_energy_delta": round(energy_delta, 4),
        "transition_flux_delta": round(flux_delta, 4),
        "transition_dynamic_delta": round(dyn_delta, 4),
        "transition_score": round(transition_score, 4),
        "transition_key_confidence": round(key_confidence, 4),
        "transition_genre_weight": round(genre_weight, 4),
        "transition_prev_camelot": c_prev,
        "transition_cur_camelot": c_cur,
    }
    return transition_score, reason


def _base_intrinsic_score(item: RankedTrack) -> float:
    bpm_component = float(item.reason.get("bpm_component", 0.0))
    energy_component = float(item.reason.get("energy_component", 0.0))
    dance_component = float(item.reason.get("danceability_component", 0.0))
    timbre_component = float(item.reason.get("timbre_component", 0.0))
    harmonic_component = float(item.reason.get("harmonic_component", 0.0))
    history_penalty_applied = float(item.reason.get("history_penalty_applied", 0.0))
    return (
        bpm_component * 0.35
        + energy_component * 0.25
        + dance_component * 0.15
        + timbre_component * 0.10
        + harmonic_component * 0.15
        - history_penalty_applied
    )


def _reliability_score(item: RankedTrack) -> float:
    score = 1.0
    ks = item.features.key_strength or 0.0
    en = item.features.energy or 0.0
    bpm = item.features.bpm
    if ks < 0.2:
        score -= 0.14
    if en < 0.03 or en > 0.97:
        score -= 0.10
    if bpm is not None and (bpm < 0.02 or bpm > 0.98):
        score -= 0.08
    return max(0.45, score)


def _normalize_artist_key(artist: str | None) -> str | None:
    if not artist:
        return None
    value = artist.strip().lower()
    return value or None


def _normalize_feedback_map(raw: dict[str, float] | None) -> dict[str, float]:
    if not raw:
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if not key:
            continue
        out[str(key).strip().lower()] = max(-1.0, min(1.0, float(value)))
    return out


def _build_candidate_pool(
    ranked: list[RankedTrack],
    target_count: int,
    target_energy_curve: str,
) -> list[RankedTrack]:
    if not ranked:
        return []

    base_pool_size = max(target_count * 8, 240) if target_energy_curve == "flat" else max(target_count * 6, 180)
    pool_size = min(len(ranked), base_pool_size)
    top_scored = ranked[:pool_size]

    bridge_sorted = sorted(
        ranked,
        key=lambda r: (
            abs((r.features.energy or 0.0) - 0.5)
            + abs((r.features.spectral_flux or 0.0) - 0.5)
            + abs((r.features.dynamic_complexity or 0.0) - 0.5)
        ),
    )
    bridge_count = min(len(ranked), max(target_count * 3, 90))
    bridge_tracks = bridge_sorted[:bridge_count]

    artist_diverse: list[RankedTrack] = []
    seen_artists: set[str] = set()
    for item in ranked:
        akey = _normalize_artist_key(item.track.artist)
        if not akey or akey in seen_artists:
            continue
        seen_artists.add(akey)
        artist_diverse.append(item)
        if len(artist_diverse) >= max(target_count * 2, 60):
            break

    merged: dict[UUID, RankedTrack] = {}
    for item in top_scored + bridge_tracks + artist_diverse:
        merged[item.track.id] = item
    return sorted(merged.values(), key=lambda r: r.score, reverse=True)


def generate_playlist(
    db: Session,
    name: str,
    limit: int,
    seed_track_id: UUID | None,
    min_bpm: float | None,
    max_bpm: float | None,
    target_energy_curve: str,
    diversity: float,
    artist_cooldown: int = 2,
    album_cooldown: int = 1,
    max_bpm_jump: float = 18.0,
    min_transition_score: float = 0.55,
    temperature: float = 0.0,
    variation_seed: int | None = None,
    history_window: int = 3,
    history_penalty: float = 0.12,
    strict_constraints: bool = False,
    genre_mode: str = "balanced",
    candidate_track_ids: list[UUID] | None = None,
    track_feedback: dict[str, float] | None = None,
    artist_feedback: dict[str, float] | None = None,
) -> dict:
    query = select(Track, TrackFeaturesNorm).join(TrackFeaturesNorm, Track.id == TrackFeaturesNorm.track_id)
    if candidate_track_ids:
        query = query.where(Track.id.in_(candidate_track_ids))
    rows = db.execute(query).all()
    if not rows:
        raise ValueError("No analyzed tracks found. Run analysis first.")

    seed = None
    if seed_track_id:
        seed = db.execute(select(TrackFeaturesNorm).where(TrackFeaturesNorm.track_id == seed_track_id)).scalar_one_or_none()

    rng = random.Random(variation_seed) if variation_seed is not None else random.Random()
    genre_weight_map = {"strict": 0.22, "balanced": 0.12, "open": 0.05}
    resolved_genre_mode = genre_mode if genre_mode in genre_weight_map else "balanced"
    genre_weight = genre_weight_map[resolved_genre_mode]
    track_feedback_map = _normalize_feedback_map(track_feedback)
    artist_feedback_map = _normalize_feedback_map(artist_feedback)
    recent_track_ids: set[UUID] = set()
    recent_first_track_ids: set[UUID] = set()
    if history_window > 0 and history_penalty > 0:
        recent_rows = db.execute(
            select(Playlist.id).order_by(Playlist.created_at.desc()).limit(history_window)
        ).all()
        recent_ids = [r[0] for r in recent_rows]
        if recent_ids:
            recent_tracks = db.execute(
                select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id.in_(recent_ids))
            ).all()
            recent_track_ids = {r[0] for r in recent_tracks}
            recent_first = db.execute(
                select(PlaylistTrack.track_id).where(
                    PlaylistTrack.playlist_id.in_(recent_ids),
                    PlaylistTrack.position == 0,
                )
            ).all()
            recent_first_track_ids = {r[0] for r in recent_first}

    ranked: list[RankedTrack] = []
    for track, feat in rows:
        bpm_component = _bpm_score(feat.bpm, min_bpm, max_bpm)
        energy_component = feat.energy or 0.0
        dance_component = feat.danceability or 0.0
        timbre_component = 1.0 - ((feat.spectral_centroid or 0.0) * diversity)
        harmonic_component = 0.5
        if seed and feat.key and feat.scale:
            harmonic_component = 1.0 if _camelot_compatible(
                _camelot_key(seed.key, seed.scale),
                _camelot_key(feat.key, feat.scale),
            ) else 0.3
        total = (
            bpm_component * 0.35
            + energy_component * 0.25
            + dance_component * 0.15
            + timbre_component * 0.10
            + harmonic_component * 0.15
        )
        track_bias = track_feedback_map.get(str(track.id).lower(), 0.0)
        artist_key = _normalize_artist_key(track.artist)
        artist_bias = artist_feedback_map.get(artist_key, 0.0) if artist_key else 0.0
        feedback_bonus = (track_bias * 0.22) + (artist_bias * 0.16)
        total += feedback_bonus
        if track.id in recent_track_ids:
            total -= history_penalty
        if temperature > 0:
            total += rng.uniform(-temperature, temperature)
        reason = {
            "bpm_component": round(bpm_component, 4),
            "energy_component": round(energy_component, 4),
            "danceability_component": round(dance_component, 4),
            "timbre_component": round(timbre_component, 4),
            "harmonic_component": round(harmonic_component, 4),
            "feedback_track_bias": round(track_bias, 4),
            "feedback_artist_bias": round(artist_bias, 4),
            "feedback_bonus": round(feedback_bonus, 4),
            "history_penalty_applied": round(history_penalty if track.id in recent_track_ids else 0.0, 4),
        }
        ranked.append(RankedTrack(track=track, features=feat, score=total, reason=reason))

    ranked.sort(key=lambda r: r.score, reverse=True)
    target_count = min(limit, len(ranked))
    selected = _build_candidate_pool(ranked, target_count, target_energy_curve)
    ordered: list[RankedTrack] = []
    pool = selected[:]
    diagnostics = {
        "requested_limit": limit,
        "generated_count": 0,
        "stopped_early": False,
        "stop_position": None,
        "resolved_target_energy_curve": target_energy_curve,
        "local_search_iterations": 0,
        "local_search_improvements": 0,
        "fallback_budget": 0,
        "fallback_used": 0,
        "adaptive_relax_pass1_used": 0,
        "adaptive_relax_pass2_used": 0,
        "reject_counts": {
            "min_transition_score": 0,
            "energy_band_flat": 0,
            "energy_band_warmup": 0,
            "energy_band_cooldown": 0,
            "fallback_weak_transition": 0,
            "artist_cap": 0,
            "album_cap": 0,
        },
    }

    # Anchor the first track based on seed or target profile behavior.
    if pool:
        first_track: RankedTrack | None = None
        if seed_track_id:
            first_track = next((c for c in pool if c.track.id == seed_track_id), None)
        if first_track is None:
            # Profile ranges for first-track anchoring (supports controlled variation).
            if target_energy_curve == "warmup":
                lo, hi = 0.05, 0.25
            elif target_energy_curve == "cooldown":
                lo, hi = 0.65, 0.95
            elif target_energy_curve == "peak":
                lo, hi = 0.40, 0.70
            else:  # flat
                lo, hi = 0.45, 0.55
            range_candidates = [c for c in pool if lo <= (c.features.energy or 0.0) <= hi]
            candidates = range_candidates if range_candidates else pool
            target_mid = (lo + hi) / 2.0
            def _first_candidate_score(c: RankedTrack) -> float:
                base = 1.0 - abs((c.features.energy or 0.0) - target_mid)
                reuse_pen = history_penalty if c.track.id in recent_track_ids else 0.0
                first_reuse_pen = min(0.30, history_penalty * 1.5) if c.track.id in recent_first_track_ids else 0.0
                jitter = rng.uniform(-temperature * 1.5, temperature * 1.5) if temperature > 0 else 0.0
                return base - reuse_pen - first_reuse_pen + jitter
            first_track = max(candidates, key=_first_candidate_score)
        assert first_track is not None
        first_target = _energy_target(target_energy_curve, 0, target_count)
        first_deviation = abs((first_track.features.energy or 0.0) - first_target)
        first_track.reason.update(
            {
                "transition_base": "seed_anchor",
                "energy_curve_target": round(first_target, 4),
                "energy_curve_deviation": round(first_deviation, 4),
                "artist_cooldown_penalty": 0.0,
                "album_cooldown_penalty": 0.0,
                "bpm_jump_penalty": 0.0,
                "first_track_range_low": round(lo, 4) if not seed_track_id else None,
                "first_track_range_high": round(hi, 4) if not seed_track_id else None,
            }
        )
        first_track.score = max(0.0, first_track.score - first_deviation * 0.25)
        first_track.reason["snapshot_path"] = first_track.track.file_path
        ordered.append(first_track)
        pool.remove(first_track)
    selected_by_id = {item.track.id: item for item in selected}

    def _energy_band_reject_key(idx: int, item: RankedTrack, relax_mode: str = "none") -> str | None:
        progress = idx / max(1, target_count - 1)
        energy_value = item.features.energy or 0.0
        target = _energy_target(target_energy_curve, idx, target_count)
        deviation = abs(energy_value - target)
        relax_flat = relax_mode in {"flat", "all1", "all2"}
        relax_all = relax_mode in {"all1", "all2"}
        relax_all2 = relax_mode == "all2"
        # Hard guardrail so sequence follows the requested energy curve closely.
        if target_energy_curve == "flat" and deviation > (0.16 if relax_flat else 0.12):
            return "energy_band_flat"
        if target_energy_curve == "warmup" and deviation > (0.32 if relax_all2 else (0.29 if relax_all else 0.26)):
            return "energy_band_warmup"
        if target_energy_curve == "cooldown" and deviation > (0.32 if relax_all2 else (0.29 if relax_all else 0.26)):
            return "energy_band_cooldown"
        if target_energy_curve == "peak" and deviation > (0.34 if relax_all2 else (0.31 if relax_all else 0.28)):
            return "energy_band_warmup"
        if target_energy_curve == "flat":
            low, high = (0.38, 0.62) if relax_flat else (0.42, 0.58)
            if not (low <= energy_value <= high):
                return "energy_band_flat"
        elif target_energy_curve == "warmup":
            if relax_all2:
                cap = 0.36 if progress <= 0.2 else (0.58 if progress <= 0.5 else (0.80 if progress <= 0.8 else 1.0))
            elif relax_all:
                cap = 0.33 if progress <= 0.2 else (0.54 if progress <= 0.5 else (0.76 if progress <= 0.8 else 1.0))
            else:
                cap = 0.30 if progress <= 0.2 else (0.50 if progress <= 0.5 else (0.72 if progress <= 0.8 else 1.0))
            if energy_value > cap:
                return "energy_band_warmup"
        elif target_energy_curve == "cooldown":
            if relax_all2:
                floor = 0.50 if progress <= 0.5 else (0.30 if progress <= 0.8 else 0.10)
            elif relax_all:
                floor = 0.53 if progress <= 0.5 else (0.32 if progress <= 0.8 else 0.12)
            else:
                floor = 0.55 if progress <= 0.5 else (0.35 if progress <= 0.8 else 0.15)
            if energy_value < floor:
                return "energy_band_cooldown"
        return None

    def _score_step(seq_ids: list[UUID], candidate: RankedTrack, idx: int, relax_mode: str = "none") -> tuple[float, bool, str | None]:
        prev = selected_by_id[seq_ids[-1]] if seq_ids else None
        target = _energy_target(target_energy_curve, idx, target_count)
        deviation = abs((candidate.features.energy or 0.0) - target)
        if target_energy_curve == "flat":
            energy_penalty_weight = 0.30
        elif target_energy_curve in {"warmup", "cooldown"}:
            energy_penalty_weight = 0.24
        else:
            energy_penalty_weight = 0.15
        transition_score, _ = _pair_transition_score(prev, candidate, genre_weight=genre_weight)
        recent_artists = [selected_by_id[t].track.artist for t in seq_ids[-artist_cooldown:] if selected_by_id[t].track.artist] if artist_cooldown > 0 else []
        artist_penalty = 0.20 if candidate.track.artist and candidate.track.artist in recent_artists else 0.0
        recent_albums = [selected_by_id[t].track.album for t in seq_ids[-album_cooldown:] if selected_by_id[t].track.album] if album_cooldown > 0 else []
        album_penalty = 0.12 if candidate.track.album and candidate.track.album in recent_albums else 0.0
        artist_count = 0
        album_count = 0
        if candidate.track.artist:
            artist_count = sum(1 for tid in seq_ids if (selected_by_id[tid].track.artist or "") == candidate.track.artist)
            if artist_count >= max_artist_tracks:
                return -1.0, False, "artist_cap"
            if artist_count > 0:
                artist_penalty += min(0.20, 0.05 * artist_count)
        if candidate.track.album:
            album_count = sum(1 for tid in seq_ids if (selected_by_id[tid].track.album or "") == candidate.track.album)
            if album_count >= max_album_tracks:
                return -1.0, False, "album_cap"
            if album_count > 0:
                album_penalty += min(0.14, 0.04 * album_count)
        bpm_jump_penalty = 0.0
        flux_delta = 0.0
        dyn_delta = 0.0
        if prev and (prev.features.bpm is not None) and (candidate.features.bpm is not None):
            bpm_delta = abs((prev.features.bpm or 0.0) - (candidate.features.bpm or 0.0))
            if bpm_delta > max_bpm_jump:
                bpm_jump_penalty = min(0.35, (bpm_delta - max_bpm_jump) / max_bpm_jump)
        if prev:
            flux_delta = abs((prev.features.spectral_flux or 0.0) - (candidate.features.spectral_flux or 0.0))
            dyn_delta = abs((prev.features.dynamic_complexity or 0.0) - (candidate.features.dynamic_complexity or 0.0))
        if target_energy_curve in {"warmup", "cooldown"}:
            early_curve_weight = 0.90 if idx <= max(1, int(target_count * 0.6)) else 0.80
        else:
            early_curve_weight = 0.75 if idx <= max(1, int(target_count * 0.4)) else 0.65
        curve_score = max(0.0, 1.0 - (deviation * (1.0 + energy_penalty_weight)))
        variety_score = max(0.0, 1.0 - artist_penalty - album_penalty)
        smoothness_score = max(0.0, 1.0 - (0.5 * flux_delta + 0.5 * dyn_delta))
        intent_score = 0.8
        if prev:
            e_prev = prev.features.energy or 0.0
            e_cur = candidate.features.energy or 0.0
            if target_energy_curve == "warmup":
                intent_score = 1.0 if e_cur >= e_prev - 0.02 else 0.5
            elif target_energy_curve == "cooldown":
                intent_score = 1.0 if e_cur <= e_prev + 0.02 else 0.5
            elif target_energy_curve == "flat":
                intent_score = max(0.0, 1.0 - abs(e_cur - 0.5) * 2.0)
            elif target_energy_curve == "peak":
                progress = idx / max(1, target_count - 1)
                should_rise = progress <= 0.5
                intent_score = 1.0 if ((should_rise and e_cur >= e_prev - 0.02) or ((not should_rise) and e_cur <= e_prev + 0.02)) else 0.5
        objective_score = (
            transition_score * 0.45
            + curve_score * 0.25
            + variety_score * 0.15
            + smoothness_score * 0.10
            + intent_score * 0.05
        )
        objective_score *= _reliability_score(candidate)
        base_blend = max(0.0, _base_intrinsic_score(candidate))
        step_score = (objective_score * 0.85 + base_blend * 0.15) * early_curve_weight + objective_score * (1.0 - early_curve_weight) - bpm_jump_penalty
        relax_transition = 0.0
        if relax_mode == "all1":
            relax_transition = 0.06
        elif relax_mode == "all2":
            relax_transition = 0.12
        transition_threshold = max(0.30, min_transition_score - relax_transition)
        if prev is not None and transition_score < transition_threshold:
            return step_score - 0.35, False, "min_transition_score"
        reject_key = _energy_band_reject_key(idx, candidate, relax_mode=relax_mode)
        if reject_key:
            return step_score - 0.35, False, reject_key
        return step_score, True, None

    initial_seq = [o.track.id for o in ordered]
    beams: list[tuple[list[UUID], float]] = [(initial_seq, 0.0)]
    beam_width = 5
    max_artist_tracks = max(1, min(6, max(2, (target_count + 7) // 8)))
    max_album_tracks = max(1, min(4, max(1, (target_count + 11) // 12)))
    fallback_budget = max(4, target_count // 6)
    fallback_used = 0
    diagnostics["fallback_budget"] = fallback_budget

    for idx in range(len(initial_seq), target_count):
        next_beams: list[tuple[list[UUID], float]] = []
        any_valid = False
        for seq_ids, seq_score in beams:
            used_ids = set(seq_ids)
            valid_expansions: list[tuple[float, UUID]] = []
            fallback_expansion: tuple[float, UUID] | None = None
            for candidate in selected:
                cid = candidate.track.id
                if cid in used_ids:
                    continue
                step_score, is_valid, reject_key = _score_step(seq_ids, candidate, idx)
                if fallback_expansion is None or step_score > fallback_expansion[0]:
                    fallback_expansion = (step_score, cid)
                if not is_valid:
                    if reject_key:
                        diagnostics["reject_counts"][reject_key] += 1
                    continue
                any_valid = True
                valid_expansions.append((step_score, cid))
            if (not valid_expansions) and target_energy_curve == "flat":
                for candidate in selected:
                    cid = candidate.track.id
                    if cid in used_ids:
                        continue
                    step_score, is_valid, _ = _score_step(seq_ids, candidate, idx, relax_mode="flat")
                    if is_valid:
                        any_valid = True
                        valid_expansions.append((step_score, cid))
            if (not valid_expansions) and target_energy_curve in {"warmup", "cooldown", "peak"} and not strict_constraints:
                for candidate in selected:
                    cid = candidate.track.id
                    if cid in used_ids:
                        continue
                    step_score, is_valid, _ = _score_step(seq_ids, candidate, idx, relax_mode="all1")
                    if is_valid:
                        any_valid = True
                        valid_expansions.append((step_score, cid))
                if valid_expansions:
                    diagnostics["adaptive_relax_pass1_used"] += 1
            if (not valid_expansions) and target_energy_curve in {"warmup", "cooldown", "peak"} and not strict_constraints:
                for candidate in selected:
                    cid = candidate.track.id
                    if cid in used_ids:
                        continue
                    step_score, is_valid, _ = _score_step(seq_ids, candidate, idx, relax_mode="all2")
                    if is_valid:
                        any_valid = True
                        valid_expansions.append((step_score, cid))
                if valid_expansions:
                    diagnostics["adaptive_relax_pass2_used"] += 1
            if valid_expansions:
                valid_expansions.sort(key=lambda x: x[0], reverse=True)
                for step_score, cid in valid_expansions[:beam_width]:
                    next_beams.append((seq_ids + [cid], seq_score + step_score))
            elif not strict_constraints and fallback_expansion is not None and fallback_used < fallback_budget:
                diagnostics["reject_counts"]["fallback_weak_transition"] += 1
                fallback_used += 1
                next_beams.append((seq_ids + [fallback_expansion[1]], seq_score + fallback_expansion[0]))
        if not next_beams:
            diagnostics["stopped_early"] = True
            diagnostics["stop_position"] = idx
            break
        next_beams.sort(key=lambda x: x[1], reverse=True)
        beams = next_beams[:beam_width]
        if strict_constraints and not any_valid:
            diagnostics["stopped_early"] = True
            diagnostics["stop_position"] = idx
            break

    diagnostics["fallback_used"] = fallback_used
    diagnostics["max_artist_tracks"] = max_artist_tracks
    diagnostics["max_album_tracks"] = max_album_tracks
    diagnostics["genre_mode"] = resolved_genre_mode
    diagnostics["genre_weight"] = genre_weight
    diagnostics["candidate_pool_size"] = len(selected)
    diagnostics["track_feedback_count"] = len(track_feedback_map)
    diagnostics["artist_feedback_count"] = len(artist_feedback_map)

    best_seq_ids = max(beams, key=lambda x: x[1])[0] if beams else initial_seq
    ordered = [selected_by_id[tid] for tid in best_seq_ids]

    def _evaluate_sequence(seq_ids: list[UUID]) -> tuple[float, int]:
        total_score = 0.0
        violations = 0
        for idx, tid in enumerate(seq_ids):
            candidate = selected_by_id[tid]
            step_score, is_valid, _ = _score_step(seq_ids[:idx], candidate, idx)
            if not is_valid:
                violations += 1
            total_score += step_score
        return total_score, violations

    # Local search refinement on top of beam output.
    if len(best_seq_ids) > 4:
        best_ids = best_seq_ids[:]
        best_total, best_violations = _evaluate_sequence(best_ids)
        move_budget = min(300, max(80, target_count * 12))
        stagnation = 0
        for _ in range(move_budget):
            diagnostics["local_search_iterations"] += 1
            improved = False
            move_kind = rng.choice(("swap", "relocate", "two_opt"))
            for _attempt in range(18):
                i = rng.randint(1, len(best_ids) - 1)
                j = rng.randint(1, len(best_ids) - 1)
                if i == j:
                    continue
                cand_ids = best_ids[:]
                if move_kind == "swap":
                    cand_ids[i], cand_ids[j] = cand_ids[j], cand_ids[i]
                elif move_kind == "relocate":
                    node = cand_ids.pop(i)
                    cand_ids.insert(j, node)
                else:  # two_opt
                    lo, hi = (i, j) if i < j else (j, i)
                    cand_ids[lo:hi + 1] = reversed(cand_ids[lo:hi + 1])
                cand_total, cand_violations = _evaluate_sequence(cand_ids)
                better = (cand_violations < best_violations) or (
                    cand_violations == best_violations and cand_total > best_total + 1e-6
                )
                if better:
                    best_ids = cand_ids
                    best_total = cand_total
                    best_violations = cand_violations
                    diagnostics["local_search_improvements"] += 1
                    improved = True
                    break
            if improved:
                stagnation = 0
            else:
                stagnation += 1
                if stagnation >= 45:
                    break
        ordered = [selected_by_id[tid] for tid in best_ids]

    # Local swap repair pass for weak adjacent transitions.
    if len(ordered) > 3:
        improved = True
        attempts = 0
        while improved and attempts < 3:
            improved = False
            attempts += 1
            for i in range(1, len(ordered) - 1):
                prev_item = ordered[i - 1]
                cur_item = ordered[i]
                next_item = ordered[i + 1]
                cur_score, _ = _pair_transition_score(prev_item, cur_item, genre_weight=genre_weight)
                next_score, _ = _pair_transition_score(cur_item, next_item, genre_weight=genre_weight)
                baseline = cur_score + next_score
                swap_score_a, _ = _pair_transition_score(prev_item, next_item, genre_weight=genre_weight)
                swap_score_b, _ = _pair_transition_score(next_item, cur_item, genre_weight=genre_weight)
                swapped = swap_score_a + swap_score_b
                if swapped > baseline + 0.12:
                    ordered[i], ordered[i + 1] = ordered[i + 1], ordered[i]
                    improved = True

    # Recompute reasons/scores from final order to keep output consistent after swap repair.
    for idx, item in enumerate(ordered):
        target = _energy_target(target_energy_curve, idx, target_count)
        deviation = abs((item.features.energy or 0.0) - target)
        if target_energy_curve == "flat":
            energy_penalty_weight = 0.30
        elif target_energy_curve in {"warmup", "cooldown"}:
            energy_penalty_weight = 0.24
        else:
            energy_penalty_weight = 0.15
        prev = ordered[idx - 1] if idx > 0 else None
        transition_score, transition_reason = _pair_transition_score(prev, item, genre_weight=genre_weight)
        recent_artists = [o.track.artist for o in ordered[max(0, idx - artist_cooldown):idx] if o.track.artist] if artist_cooldown > 0 else []
        artist_penalty = 0.20 if item.track.artist and item.track.artist in recent_artists else 0.0
        recent_albums = [o.track.album for o in ordered[max(0, idx - album_cooldown):idx] if o.track.album] if album_cooldown > 0 else []
        album_penalty = 0.12 if item.track.album and item.track.album in recent_albums else 0.0
        if item.track.artist:
            artist_count = sum(1 for o in ordered[:idx] if (o.track.artist or "") == item.track.artist)
            if artist_count > 0:
                artist_penalty += min(0.20, 0.05 * artist_count)
        if item.track.album:
            album_count = sum(1 for o in ordered[:idx] if (o.track.album or "") == item.track.album)
            if album_count > 0:
                album_penalty += min(0.14, 0.04 * album_count)
        bpm_jump_penalty = 0.0
        flux_delta = 0.0
        dyn_delta = 0.0
        if prev and (prev.features.bpm is not None) and (item.features.bpm is not None):
            bpm_delta = abs((prev.features.bpm or 0.0) - (item.features.bpm or 0.0))
            if bpm_delta > max_bpm_jump:
                bpm_jump_penalty = min(0.35, (bpm_delta - max_bpm_jump) / max_bpm_jump)
        if prev:
            flux_delta = abs((prev.features.spectral_flux or 0.0) - (item.features.spectral_flux or 0.0))
            dyn_delta = abs((prev.features.dynamic_complexity or 0.0) - (item.features.dynamic_complexity or 0.0))
        if target_energy_curve in {"warmup", "cooldown"}:
            early_curve_weight = 0.90 if idx <= max(1, int(target_count * 0.6)) else 0.80
        else:
            early_curve_weight = 0.75 if idx <= max(1, int(target_count * 0.4)) else 0.65
        curve_score = max(0.0, 1.0 - (deviation * (1.0 + energy_penalty_weight)))
        variety_score = max(0.0, 1.0 - artist_penalty - album_penalty)
        smoothness_score = max(0.0, 1.0 - (0.5 * flux_delta + 0.5 * dyn_delta))
        intent_score = 0.8
        if prev:
            e_prev = prev.features.energy or 0.0
            e_cur = item.features.energy or 0.0
            if target_energy_curve == "warmup":
                intent_score = 1.0 if e_cur >= e_prev - 0.02 else 0.5
            elif target_energy_curve == "cooldown":
                intent_score = 1.0 if e_cur <= e_prev + 0.02 else 0.5
            elif target_energy_curve == "flat":
                intent_score = max(0.0, 1.0 - abs(e_cur - 0.5) * 2.0)
            elif target_energy_curve == "peak":
                progress = idx / max(1, target_count - 1)
                should_rise = progress <= 0.5
                intent_score = 1.0 if ((should_rise and e_cur >= e_prev - 0.02) or ((not should_rise) and e_cur <= e_prev + 0.02)) else 0.5
        objective_score = (
            transition_score * 0.45
            + curve_score * 0.25
            + variety_score * 0.15
            + smoothness_score * 0.10
            + intent_score * 0.05
        )
        base_blend = max(0.0, _base_intrinsic_score(item))
        item.score = (objective_score * 0.85 + base_blend * 0.15) * early_curve_weight + objective_score * (1.0 - early_curve_weight) - bpm_jump_penalty
        if prev is not None and transition_score < min_transition_score:
            item.score -= 0.20
        if _energy_band_reject_key(idx, item):
            item.score -= 0.20
        reliability_score = _reliability_score(item)
        item.score *= reliability_score
        item.reason.update(
            {
                "energy_curve_target": round(target, 4),
                "energy_curve_deviation": round(deviation, 4),
                "curve_score": round(curve_score, 4),
                "variety_score": round(variety_score, 4),
                "smoothness_score": round(smoothness_score, 4),
                "intent_score": round(intent_score, 4),
                "objective_score": round(objective_score, 4),
                "reliability_score": round(reliability_score, 4),
                "artist_cooldown_penalty": round(artist_penalty, 4),
                "album_cooldown_penalty": round(album_penalty, 4),
                "bpm_jump_penalty": round(bpm_jump_penalty, 4),
                **transition_reason,
            }
        )
        item.reason["snapshot_path"] = item.track.file_path

    playlist = Playlist(
        name=name,
        constraints_json={
            "limit": limit,
            "seed_track_id": str(seed_track_id) if seed_track_id else None,
            "min_bpm": min_bpm,
            "max_bpm": max_bpm,
            "target_energy_curve": target_energy_curve,
            "diversity": diversity,
            "artist_cooldown": artist_cooldown,
            "album_cooldown": album_cooldown,
            "max_bpm_jump": max_bpm_jump,
            "min_transition_score": min_transition_score,
            "temperature": temperature,
            "variation_seed": variation_seed,
            "history_window": history_window,
            "history_penalty": history_penalty,
            "strict_constraints": strict_constraints,
            "genre_mode": resolved_genre_mode,
            "max_artist_tracks": max_artist_tracks,
            "max_album_tracks": max_album_tracks,
            "track_feedback_count": len(track_feedback_map),
            "artist_feedback_count": len(artist_feedback_map),
        },
        explanation_json={"strategy": "weighted_rule_scoring", "version": "v1"},
    )
    db.add(playlist)
    db.flush()

    output_tracks = []
    for position, item in enumerate(ordered):
        db.add(
            PlaylistTrack(
                playlist_id=playlist.id,
                track_id=item.track.id,
                position=position,
                score=float(round(item.score, 6)),
                reason_json=item.reason,
            )
        )
        output_tracks.append(
            {
                "position": position,
                "track_id": str(item.track.id),
                "file_path": item.track.file_path,
                "title": item.track.title,
                "artist": item.track.artist,
                "genre": item.track.genre,
                "score": round(item.score, 6),
                "energy": round(item.features.energy or 0.0, 6),
                "bpm": round(item.features.bpm or 0.0, 6),
                "key_strength": round(item.features.key_strength or 0.0, 6),
                "spectral_flux": round(item.features.spectral_flux or 0.0, 6),
                "dynamic_complexity": round(item.features.dynamic_complexity or 0.0, 6),
                "reason": item.reason,
            }
        )
    db.commit()
    diagnostics["generated_count"] = len(output_tracks)
    diagnostics["requested_limit"] = limit
    return {
        "playlist_id": str(playlist.id),
        "name": playlist.name,
        "tracks": output_tracks,
        "generation_diagnostics": diagnostics,
    }


def compute_playlist_quality(db: Session, playlist_id: UUID) -> dict:
    rows = db.execute(
        select(PlaylistTrack, TrackFeaturesNorm, Track)
        .join(TrackFeaturesNorm, TrackFeaturesNorm.track_id == PlaylistTrack.track_id)
        .join(Track, Track.id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position.asc())
    ).all()
    if not rows:
        raise ValueError("Playlist not found or empty")

    bpm_deltas = []
    harmonic_hits = 0
    energy_deltas = []
    timbre_deltas = []
    artist_repeats = 0
    genre_matches = 0
    genre_comparable = 0
    prev = None
    artists = []
    for _, feat, track in rows:
        artists.append(track.artist or "")
        if prev is not None:
            prev_feat, prev_track = prev
            bpm_deltas.append(abs((feat.bpm or 0.0) - (prev_feat.bpm or 0.0)))
            energy_deltas.append(abs((feat.energy or 0.0) - (prev_feat.energy or 0.0)))
            timbre_deltas.append(abs((feat.spectral_centroid or 0.0) - (prev_feat.spectral_centroid or 0.0)))
            if _camelot_compatible(_camelot_key(prev_feat.key, prev_feat.scale), _camelot_key(feat.key, feat.scale)):
                harmonic_hits += 1
            if (track.artist or "") == (prev_track.artist or ""):
                artist_repeats += 1
            g_prev = _normalize_genre(prev_track.genre)
            g_cur = _normalize_genre(track.genre)
            if g_prev and g_cur:
                genre_comparable += 1
                if g_prev == g_cur or g_prev in g_cur or g_cur in g_prev:
                    genre_matches += 1
        prev = (feat, track)

    transitions = max(1, len(rows) - 1)
    unique_artists = len(set(a for a in artists if a))
    return {
        "tracks_count": len(rows),
        "mean_adjacent_bpm_delta": round(mean(bpm_deltas), 4) if bpm_deltas else 0.0,
        "harmonic_compatibility_rate": round(harmonic_hits / transitions, 4),
        "mean_adjacent_energy_delta": round(mean(energy_deltas), 4) if energy_deltas else 0.0,
        "mean_adjacent_timbre_delta": round(mean(timbre_deltas), 4) if timbre_deltas else 0.0,
        "adjacent_artist_repeat_rate": round(artist_repeats / transitions, 4),
        "artist_diversity_ratio": round(unique_artists / max(1, len(rows)), 4),
        "genre_coherence_rate": round(genre_matches / genre_comparable, 4) if genre_comparable else 0.0,
    }
