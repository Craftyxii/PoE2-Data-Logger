"""Read opened Runeshape rewards and compare their order with stored families.

Native row OCR precedes the RapidOCR fallback. Visible socket geometry is
checked independently of recipe counts, and returned eligibility flags keep
heading, sequence and socket conflicts available to callers for review."""

from __future__ import annotations

import re
import threading
import hashlib
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import ocr_runtime
from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.ocr import runehelper_ocr

OCR_LOCK = threading.Lock()
MODEL_HASHES = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
}


def verify_models(model_root):
    """Reject missing or changed bundled RapidOCR models by their expected SHA-256 hashes."""
    for filename, expected in MODEL_HASHES.items():
        path = Path(model_root) / filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("A bundled OCR model is missing or damaged. Reinstall the latest logger.")


def scan_both(path, strictness=ocr_sensitivity.DEFAULT, seed_strictness=None):
    """Choose opened rewards or seed bars using their independently frozen strictness."""
    from PoE2_Data_Logger.ocr.scan import scan
    strictness = ocr_sensitivity.validate(strictness)
    seed_strictness = ocr_sensitivity.validate(strictness if seed_strictness is None else seed_strictness)
    options = {} if strictness == ocr_sensitivity.DEFAULT else {"strictness": strictness}
    seed_options = {} if seed_strictness == ocr_sensitivity.DEFAULT else {"strictness": seed_strictness}
    opened = scan_opened(path, allow_fallback=False, **options)
    if opened.get("first_recipe") or opened.get("opened_recipes"):
        return {**opened, "mode": "opened", "scan_selection": "both"}
    seeds = scan(path, **seed_options)
    if seeds.get("remnants"):
        return {**seeds, "mode": "seed", "scan_selection": "both"}
    opened = scan_opened(path, **options)
    if opened.get("first_recipe") or opened.get("opened_recipes"):
        return {**opened, "mode": "opened", "scan_selection": "both"}
    return {**seeds, "mode": "seed", "scan_selection": "both"}


def _key(value):
    """Normalize recipe or heading text to lowercase alphanumerics for comparison."""
    return re.sub(r"[^a-z0-9]", "", value.lower().replace("’", "'").replace("‘", "'"))


def _quantity(name):
    """Extract supported recipe quantity prefixes/suffixes, defaulting to one."""
    match = re.search(r"\s+x(\d+)$", name, flags=re.I) or re.match(r"^\s*(\d+)\s*[x×]\s*", name, flags=re.I)
    return int(match.group(1)) if match else 1


def _levels(name):
    """Collect explicit level numbers so fuzzy matches cannot change skill level."""
    return tuple(int(level) for level in re.findall(r"\blevel\s*(\d+)\b", name, flags=re.I))


def _match(db, text, quantity, names, strictness=ocr_sensitivity.DEFAULT):
    """Prefer canonical spelling, then adjust the default .82 review similarity floor.

    Keep the .025 ambiguity lead and every candidate's requested quantity and
    explicit skill levels, including when lower strictness broadens suggestions.
    """
    target = f"{text} x{quantity}" if quantity > 1 else text
    levels = _levels(text)
    for spelling in (target, f"{quantity}x {text}"):
        try:
            exact = logger._canonical(db, spelling)
            if _quantity(exact) == quantity and _levels(exact) == levels:
                return exact, 1.0
        except ValueError:
            pass
    target_key = _key(target)
    choices = sorted(((SequenceMatcher(None, target_key, _key(name)).ratio(), name)
                      for name in names if _quantity(name) == quantity and _levels(name) == levels), reverse=True)
    if not choices:
        return None, 0.0
    top, name = choices[0]
    runner = choices[1][0] if len(choices) > 1 else 0
    if top < ocr_sensitivity.review_threshold(.82, strictness, .12) or top - runner < .025:
        return None, round(top, 2)
    return name, round(top, 2)


def _families(db, lines, list_complete=False):
    """Compare recognized reward order against valid family sequences starting at any stage.

    A blank-ended list can narrow candidates to exact suffixes; one fully
    matched candidate yields a family without implying screenshot correctness.
    """
    first = lines[0]["recipe"] if lines else None
    if not first:
        return None, [], False
    matches = []
    for row in db.execute("SELECT id,recipes_json FROM families WHERE valid=1"):
        sequence = logger._load(row["recipes_json"])
        for start, name in enumerate(sequence):
            if name != first:
                continue
            length = 1
            for offset, line in enumerate(lines[1:], 1):
                if start + offset >= len(sequence) or line["recipe"] != sequence[start + offset]:
                    break
                length += 1
            matches.append((length, row["id"], len(sequence) - start == len(lines)))
    if not matches:
        return None, [], False
    best = max(length for length, _, _ in matches)
    exact = {family for length, family, ends in matches if length == len(lines) and ends}
    candidates = sorted(exact if list_complete and exact else
                        {family for length, family, _ in matches if length == best})
    complete = best == len(lines) and all(line["recipe"] for line in lines)
    return candidates[0] if complete and len(candidates) == 1 else None, candidates, complete


def _list_complete(image, lines, right):
    """Check unused parchment at reference scale so text margins, frame edges and ink
    runs have the same meaning on enlarged captures.
    """
    if not lines:
        return False
    if image.width >= 900 and image.height >= 600 and image.width >= image.height * 1.25:
        # Native OCR keeps reward coordinates in the full game window. Search
        # the same narrow panel area as the socket reader, so blank parchment
        # is not confused with the game scene to its right.
        right = min(right, round(image.height * .70))
    panel = image.crop((0, 0, min(image.width, int(right)), image.height)).convert("RGB")
    import cv2
    gray = cv2.cvtColor(np.asarray(panel), cv2.COLOR_RGB2GRAY)
    left, _, edge, bottom = runehelper_ocr._find_panel(gray)
    scale = (edge - left) / 575
    if scale > 1:
        gray = cv2.resize(gray, (round(gray.shape[1] / scale), round(gray.shape[0] / scale)),
                          interpolation=cv2.INTER_AREA)
        _, _, edge, bottom = runehelper_ocr._find_panel(gray)
    else:
        scale = 1
    top = int(lines[-1]["y"] / scale + 45)
    bottom -= 12
    if bottom - top < 120:
        return False
    sample = gray[top:bottom, max(0, round(edge * .23)):max(1, round(edge * .88))]
    if not sample.size:
        return False
    low, median, high = np.percentile(sample, [25, 50, 95])
    if low < 75 or not 95 <= median <= 205 or high > 235:
        return False
    ink = (sample < min(75, median * .52)).mean(axis=1)
    # Short right-aligned reward names occupy little of this wide parchment
    # sample. Three sustained rows still distinguish their ink from isolated
    # frame noise without treating an unread short reward as empty space.
    return not any(np.convolve((ink > .04).astype(np.int8), np.ones(3), "valid") >= 3)


def _reference_icon_count(image, reward_y):
    """Count contiguous high-variance icons at fixed panel coordinates as a contour fallback.

    Require at least three and reject a later strong icon after a gap.
    """
    center_x = 55 if image.width < 700 else 70
    center_y = int(reward_y - 27)
    strengths = []
    for index in range(10):
        x = center_x + 41 * index
        if x - 13 < 0 or center_y - 13 < 0 or x + 13 > image.width or center_y + 13 > image.height:
            return None
        strengths.append(float(np.asarray(image.crop((x - 13, center_y - 13, x + 13, center_y + 13))).std()))
    count = 0
    for strength in strengths:
        if strength <= 32:
            break
        count += 1
    return count if count >= 3 and not any(strength > 38 for strength in strengths[count + 1:]) else None


def _icon_count(image, reward_y, expected=None):
    """Count visible sockets at reference scale with room for OCR reward-row position jitter.

    Keep complete frame contours in the search band; their centers, spacing and
    size determine the count independently of database expectations.
    """
    import cv2
    if image.width >= 900 and image.height >= 600 and image.width >= image.height * 1.25:
        window = image.crop((0, 0, min(image.width, round(image.height * .70)), image.height))
        gray = cv2.cvtColor(np.asarray(window.convert("RGB")), cv2.COLOR_RGB2GRAY)
        left, top, right, bottom = runehelper_ocr._find_panel(gray)
        if right < window.width:
            # Socket geometry belongs to the panel, not the full game window.
            # Normalize its width before sampling the icon row.
            panel = window.crop((0, top, right, bottom))
            scale = 575 / panel.width
            panel = panel.resize((575, round(panel.height * scale)), Image.Resampling.LANCZOS)
            return _icon_count(panel, (reward_y - top) * scale, expected)
    elif image.width != 575:
        # Cropped captures can arrive at any display scale, too. Small icon
        # borders lose contours at their original size and the fixed reference
        # sampler otherwise examines different positions. Count at the same
        # reference width used for full-window captures, independently of DB.
        scale = 575 / image.width
        image = image.resize((575, max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        reward_y *= scale
    reference = _reference_icon_count(image, reward_y)
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    left, _, right, _ = runehelper_ocr._find_panel(gray)
    tile = (right - left) * .07
    if tile < 10:
        return None
    # One band ends above reward text; the padded band retains frame bottoms
    # when OCR shifts upward. Merge their frame evidence before counting so
    # either crop boundary cannot remove a socket. Center distances exclude
    # reward text that enters the padded band.
    top = max(0, round(reward_y - tile * 1.8))
    boxes = []
    frame_centres = []
    for edge_y in (reward_y - 2, reward_y + tile * .25):
        bottom = min(image.height, round(edge_y))
        if bottom <= top:
            continue
        edges = cv2.Canny(gray[top:bottom, left:right], 60, 130)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if (tile * .65 <= w <= tile * 1.4 and tile * .55 <= h <= tile * 1.4 and .65 <= w / h <= 1.5
                    and tile * .25 <= reward_y - (top + y + h / 2) <= tile * 1.05):
                # The selected-row diamond also fits a socket's bounding box.
                # A row anchor must have the rectangular support of a frame;
                # keep open contours farther along the row as frame evidence.
                rectangular = cv2.contourArea(cv2.convexHull(contour)) >= w * h * .70
                if x + w / 2 < tile * 2 and not rectangular:
                    continue
                boxes.append((left + x + w / 2, top + y + h / 2, w * h))
                if rectangular:
                    frame_centres.append((left + x + w / 2, top + y + h / 2))
    centres = []
    for x, y, area in sorted(boxes, key=lambda box: -box[2]):
        if not any(abs(x - a) < tile * .65 and abs(y - b) < tile * .65 for a, b in centres):
            centres.append((x, y))
    for x, y in sorted(centres):
        # A detected parchment edge must not move the first-socket anchor
        # into column two when the actual first frame has a broken border.
        if not left <= x <= image.width * .07 * 2:
            continue
        candidates = sorted(a for a, b in centres if abs(b - y) < tile * .25 and a >= x - tile * .2)
        row = []
        for centre in candidates:
            # A reward label can contain a tile-sized text contour in the
            # same vertical band. Its distant bounding box is not another
            # socket and must not invalidate the contiguous rune frames.
            if row and centre - row[-1] > tile * 1.65:
                break
            row.append(centre)
        if not 3 <= len(row) <= 10:
            continue
        gaps = np.diff(row)
        if np.all((gaps >= tile * .80) & (gaps <= tile * 1.65)) and gaps.max() - gaps.min() < tile * .30:
            # Broken borders can omit an interior contour. A short prefix is
            # not the complete row when later frames still fit its spacing.
            # Corroborate every intervening socket at the established frame
            # centres, using the reference sampler's independent ink check.
            pitch = float(np.median(gaps))
            later = [centre for centre in candidates if centre > row[-1] + tile * 1.65
                     and len(row) <= round((centre - x) / pitch) < 10
                     and abs(centre - (x + pitch * round((centre - x) / pitch))) < tile * .30
                     # A distant text outline can land on the socket grid.
                     # Unlike a nearby broken border, it needs independent
                     # rectangular frame support to prove row continuation.
                     and (centre - row[-1] <= tile * 1.65 * 2
                          or any(abs(centre - a) < tile * .65 and abs(y - b) < tile * .65
                                 for a, b in frame_centres))]
            if later:
                strengths = []
                for index in range(len(row), 10):
                    centre = x + pitch * index
                    if centre + 13 > image.width or y - 13 < 0 or y + 13 > image.height:
                        break
                    strengths.append(float(np.asarray(image.crop((round(centre - 13), round(y - 13),
                                                                   round(centre + 13), round(y + 13)))).std()))
                count = len(row)
                for strength in strengths:
                    if strength <= 32:
                        break
                    count += 1
                # A genuinely empty socket position followed by a frame or
                # strong ink is incomplete geometry, not a shorter remnant.
                if (any(round((centre - x) / pitch) >= count for centre in later)
                        or count != round((later[-1] - x) / pitch) + 1
                        or any(strength > 38 for strength in strengths[count - len(row) + 1:])):
                    return None
                return count
            return len(row)
    return reference


@lru_cache(maxsize=1)
def _engine():
    """Cache a verified shared RapidOCR engine configured for the active OCR thread count.

    Callers own OCR_LOCK while invoking this engine or its recognizer.
    """
    try:
        import rapidocr
        from rapidocr import RapidOCR
    except ImportError as error:
        raise RuntimeError("Opened-remnant OCR needs RapidOCR. Run Install Dependencies.bat again.") from error
    model_root = Path(rapidocr.__file__).resolve().parent / "models"
    models = {"Det": "PP-OCRv6_det_small.onnx", "Rec": "PP-OCRv6_rec_small.onnx",
              "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx"}
    verify_models(model_root)
    return RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": ocr_runtime.active_threads(),
                            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                            **{f"{kind}.model_path": str(model_root / filename)
                               for kind, filename in models.items()}})


def _panel_text(image, top, right, bottom):
    """Read a bounded panel strip at its captured size and restore absolute row coordinates."""
    strip = image.crop((0, top, right, bottom))
    canvas = Image.new("RGB", (strip.width, max(strip.width, strip.height)), (190, 190, 190))
    canvas.paste(strip, (0, 0))
    with OCR_LOCK:
        found = _engine()(canvas)
    rows = []
    if found.boxes is not None:
        for box, text, confidence in zip(found.boxes, found.txts, found.scores):
            xs, ys = [float(p[0]) for p in box], [float(p[1]) + top for p in box]
            rows.append({"text": text.strip(), "score": float(confidence),
                         "x1": min(xs), "x2": max(xs), "y1": min(ys), "y2": max(ys)})
    return rows


def _recover_top_reward(image, detections, lines, title, header_rows, right,
                        db, names, reward, skill, strictness):
    """Recover one captured top reward without inferring its name, quantity or level from a family.

    Reuse the heading pass when native OCR skipped a row. Retry only an
    unmatched first row, requiring one overlapping, confident prefixed reading
    with the same quantity and any already explicit levels. Keep its original
    geometry and retain the original hold on weak, conflicting or ambiguous evidence.
    """
    scale = right / 575
    floor = ocr_sensitivity.clear_threshold(.8, strictness, .2)
    options = {} if strictness == ocr_sensitivity.DEFAULT else {"strictness": strictness}

    def matched(row):
        """Match actual reward text through the unchanged quantity, level and ambiguity rules."""
        found, found_skill = reward.match(row["text"]), skill.match(row["text"])
        if row["score"] < floor or not (found or found_skill):
            return None
        count = (1 if found.group(1) in ("I", "l") else int(found.group(1))) if found else 1
        text = (found.group(2).strip() if found else
                f"{found_skill.group(2).strip()} (Level {found_skill.group(1)})")
        recipe, score = _match(db, text, count, names, **options)
        if recipe is None:
            return None
        confidence = round(row["score"], 2)
        if strictness != ocr_sensitivity.DEFAULT:
            confidence = min(confidence, row["score"])
        return {"raw": row["text"], "quantity": count, "text": text,
                "ocr_score": confidence, "y": row["y1"],
                "recipe": recipe, "match_score": score}

    first = lines[0]
    if first["y"] - title["y2"] > 90 * scale:
        earlier = [row for row in header_rows
                   if row["y1"] >= title["y2"] + 16 * scale
                   and row["y2"] < first["y"] and 0 <= row["x1"] < row["x2"] <= right
                   and (reward.match(row["text"]) or skill.match(row["text"]))]
        recovered = matched(earlier[0]) if len(earlier) == 1 else None
        if recovered:
            lines.insert(0, recovered)
        return
    if first["recipe"] is not None:
        return
    native = next((row for row in detections if row["y1"] == first["y"]), None)
    if native is None or not (reward.match(first["raw"]) or skill.match(first["raw"])):
        return
    top = max(0, round(title["y2"] + 16 * scale))
    bottom = min(image.height, round(native["y2"] + 16 * scale),
                 round(lines[1]["y"] - 16 * scale) if len(lines) > 1 else image.height)
    if bottom <= top:
        return
    rows = [row for row in _panel_text(image, top, right, bottom)
            if top <= row["y1"] < row["y2"] <= bottom and 0 <= row["x1"] < row["x2"] <= right
            and min(row["y2"], native["y2"]) > max(row["y1"], native["y1"])
            and (reward.match(row["text"]) or skill.match(row["text"]))]
    recovered = matched(rows[0]) if len(rows) == 1 else None
    if (recovered and recovered["quantity"] == first["quantity"]
            and (not _levels(first["text"]) or _levels(first["text"]) == _levels(recovered["text"]))):
        recovered["y"] = first["y"]
        lines[0] = recovered


def scan_opened(path: Path | Image.Image, ocr_rows=None, allow_fallback=True, verify_header=True,
                strictness=ocr_sensitivity.DEFAULT):
    """Read ordered opened rewards, match recipes/families and check visible socket counts.

    Use native rows first, optionally verify the heading or run RapidOCR fallback,
    and hold incomplete sequences or socket conflicts in the returned review status.
    allow_fallback controls the full-image fallback; verified native panels can
    reuse heading text or retry one unmatched top row within that panel.
    can_use expresses matched geometry/header constraints, not approval to log.
    Lower capture strictness permits tentative known matches; semantic and
    geometry checks still hold them for review when evidence conflicts.
    """
    strictness = ocr_sensitivity.validate(strictness)
    options = {} if strictness == ocr_sensitivity.DEFAULT else {"strictness": strictness}
    heading_threshold = ocr_sensitivity.clear_threshold(.8, strictness, .15)
    if isinstance(path, Image.Image):
        image = path.convert("RGB")
    else:
        with Image.open(path) as source:
            image = source.convert("RGB")
    detections = runehelper_ocr.recognize(image, **options) if ocr_rows is None else []
    header_verified = False
    header_captured = False
    header_rows = []
    header_right = None
    reward = re.compile(r"^\s*(\d{1,3}|[Il])\s*[xX×]\s+(.+?)\s*$")
    skill = re.compile(r"^Skill Level\s*(\d{1,2})\s*:\s*(.+?)\s*$", re.I)
    if detections and any(reward.match(row["text"]) or skill.match(row["text"]) for row in detections):
        first_y = min(row["y1"] for row in detections)
        title = {"x1": 0, "x2": image.width // 2, "y1": max(0, first_y - 32),
                 "y2": first_y - 16}
        if verify_header:
            # Native rows end at the detected panel edge. Restrict heading
            # OCR to that panel so a large game window cannot shrink the
            # title out of the detector's usable resolution. Search margins
            # scale with the panel, while returned boxes stay capture-based.
            header_right = min(image.width, max(row["x2"] for row in detections))
            header_scale = header_right / 575
            top = max(0, round(first_y - 180 * header_scale))
            header_rows = _panel_text(image, top, header_right,
                                      max(top + 1, round(first_y - 8 * header_scale)))
            heading = next((row for row in header_rows
                            if "runeshapecombinations" in _key(row["text"])
                            and row["score"] >= heading_threshold), None)
            if heading:
                title = {key: heading[key] for key in ("x1", "x2", "y1", "y2")}
                header_verified = True
            # Padding keeps the detector at reference size; it is not captured
            # reward evidence and must not introduce a missing top row.
            header_rows = [row for row in header_rows
                           if top <= row["y1"] < row["y2"] <= round(first_y - 8 * header_scale)]
            header_captured = (heading is not None and heading in header_rows
                               and 0 <= heading["x1"] < heading["x2"] <= header_right)
        right = image.width
    else:
        detections = []
        if ocr_rows is not None:
            detections = [{"text": row["text"], "score": row["score"],
                           "x1": row["x"], "y1": row["y"],
                           "x2": row["right"], "y2": row["bottom"]}
                          for group in ocr_rows for row in (group.get("parts") or [group])]
        elif allow_fallback:
            with OCR_LOCK:
                result = _engine()(image)
            if result.boxes is not None:
                for box, text, confidence in zip(result.boxes, result.txts, result.scores):
                    xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
                    detections.append({"text": text.strip(), "score": float(confidence),
                                       "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)})
        title = next((line for line in detections
                      if "runeshapecombinations" in _key(line["text"]) and line["score"] >= heading_threshold), None)
        header_verified = title is not None
        right = min(image.width, (title["x1"] + title["x2"]) + 20) if title else image.width
    if title is None:
        return {"mode": "opened", "status": "Opened remnant panel not found — review manually.",
                "opened_recipes": [], "first_recipe": None, "next_recipe": None,
                "sockets": None, "family": None, "candidates": []}
    lines = []
    for line in detections:
        if strictness > ocr_sensitivity.DEFAULT and line["score"] < ocr_sensitivity.clear_threshold(.8, strictness, .2):
            continue
        if line["y1"] < title["y2"] + 16 or line["x2"] > right + 16:
            continue
        found = reward.match(line["text"])
        found_skill = skill.match(line["text"])
        if found:
            count = 1 if found.group(1) in ("I", "l") else int(found.group(1))
            name = found.group(2).strip()
        elif found_skill:
            count, name = 1, f"{found_skill.group(2).strip()} (Level {found_skill.group(1)})"
        elif line["x1"] > 120 and len(line["text"]) >= 8:
            count, name = 1, line["text"]
        else:
            continue
        # Keep default rounding unchanged; newly admitted weak rows must not
        # round upward through the automatic approval floor.
        score = round(line["score"], 2)
        if strictness != ocr_sensitivity.DEFAULT:
            score = min(score, line["score"])
        lines.append({"raw": line["text"], "quantity": count,
                      "text": name, "ocr_score": score,
                      "y": line["y1"]})
    lines.sort(key=lambda item: item["y"])
    with logger._connect() as db:
        names = [row[0] for row in db.execute("SELECT name FROM recipes")]
        for line in lines:
            line["recipe"], line["match_score"] = _match(db, line["text"], line["quantity"], names, **options)
        lines = [line for line in lines if reward.match(line["raw"]) or
                 skill.match(line["raw"]) or (line["recipe"] and line["match_score"] >=
                                             ocr_sensitivity.review_threshold(.92, strictness, .08))]
        if header_verified and header_captured and header_right is not None and lines:
            _recover_top_reward(image, detections, lines, title, header_rows, header_right,
                                db, names, reward, skill, strictness)
        first = lines[0]["recipe"] if lines else None
        second = lines[1]["recipe"] if len(lines) > 1 else None
        list_complete = _list_complete(image, lines, right)
        family, candidates, complete = _families(db, lines, list_complete)
        sockets = db.execute("SELECT sockets FROM recipes WHERE name=?", (first,)).fetchone() if first else None
    bottom = int(lines[-1]["y"] + 65) if lines else int(title["y2"] + 180)
    visible_sockets = _icon_count(image, lines[0]["y"], sockets[0] if sockets else None) if lines else None
    first_line_gap = (round(lines[0]["y"] - title["y2"])
                      if lines and (header_verified or not verify_header) else None)
    recipe_sockets = sockets[0] if sockets else None
    socket_conflict = bool(visible_sockets and recipe_sockets and visible_sockets != recipe_sockets)
    for line in lines:
        line.pop("text")
        line.pop("quantity")
        line.pop("y")
    if not lines:
        status = "No reward lines found in the opened panel — review manually."
    elif verify_header and not header_verified:
        status = "Opened remnant heading not found — review manually."
    elif not first:
        status = "Top reward was unclear — review the OCR text before logging."
    elif any(line["recipe"] is None for line in lines):
        status = "Some reward lines were unclear — review before logging."
    elif socket_conflict:
        status = (f"Opened icons show {visible_sockets} sockets, but Recipe DB says "
                  f"{recipe_sockets} for {first}. Review before logging.")
    elif not candidates or not complete:
        status = "Opened rewards conflict with the family database — review before logging."
    elif len(candidates) > 1:
        status = "Opened rewards match multiple families — choose the correct family."
    else:
        status = "Review the opened rewards before logging."
    return {"mode": "opened", "status": status, "opened_recipes": lines,
            "first_recipe": first, "next_recipe": second,
            "sockets": visible_sockets or recipe_sockets, "recipe_sockets": recipe_sockets,
            "socket_source": "opened icons" if visible_sockets else "Recipe DB",
            "first_line_gap": first_line_gap,
            "header_verified": header_verified,
            "list_complete": list_complete,
            "family": f"Family {family}" if family else None,
            "candidates": candidates,
            "can_use": complete and not socket_conflict and (header_verified or not verify_header),
            **({"_ocr_strictness": strictness} if strictness != ocr_sensitivity.DEFAULT else {}),
            "bar_bounds": {"x": 0, "y": max(0, int(title["y1"])-20),
                           "width": int(right),
                           "height": max(120, min(image.height, bottom) - max(0, int(title["y1"])-20))}}
