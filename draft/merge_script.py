import os

with open('sv_dual_callback_withdepth.py', 'r') as f:
    depth_lines = f.readlines()

with open('sv_dual_callback_v2.py', 'r') as f:
    v2_lines = f.readlines()

# We need to construct the new content for sv_dual_callback_withdepth.py
new_lines = []

# Chunk 1: imports and boundaries
# Find where LEFT_BOUNDARY starts in depth_lines
idx_bounds_start = 0
for i, line in enumerate(depth_lines):
    if line.startswith("LEFT_BOUNDARY = 0.15"):
        idx_bounds_start = i
        break

# Insert up to bounds start, but adding import time
for i in range(idx_bounds_start):
    new_lines.append(depth_lines[i])
    if depth_lines[i].startswith("import os"):
        new_lines.append("import time\n")

# Add the boundaries logic
new_lines.append("# Object detection version\n")
new_lines.append("# LEFT_BOUNDARY = 0.22\n")
new_lines.append("# CENTER_LEFT_BOUNDARY = 0.25\n")
new_lines.append("# CENTER_RIGHT_BOUNDARY = 0.75\n")
new_lines.append("# RIGHT_BOUNDARY = 0.78\n\n")

new_lines.append("# Depth estimation version (used to make depth estimation work)\n")
new_lines.append("LEFT_BOUNDARY = 0.15\n")
new_lines.append("CENTER_LEFT_BOUNDARY = 0.20\n")
new_lines.append("CENTER_RIGHT_BOUNDARY = 0.80\n")
new_lines.append("RIGHT_BOUNDARY = 0.85\n\n")
new_lines.append("CONFIDENCE_THRESHOLD = 0.70\n")

# Skip the original boundaries in depth_lines
idx_bounds_end = idx_bounds_start
while not depth_lines[idx_bounds_end].startswith("hailo_logger ="):
    idx_bounds_end += 1

# Chunk 2: user_app_callback_class
# Find user_app_callback_class in depth_lines
idx_class_start = 0
for i in range(idx_bounds_end, len(depth_lines)):
    if depth_lines[i].startswith("class user_app_callback_class"):
        idx_class_start = i
        break

# Add lines from hailo_logger to class definition
for i in range(idx_bounds_end, idx_class_start):
    new_lines.append(depth_lines[i])

# Find get_depth_stats in depth_lines
idx_get_depth_stats = 0
for i in range(idx_class_start, len(depth_lines)):
    if depth_lines[i].strip().startswith("def get_depth_stats"):
        idx_get_depth_stats = i
        break

# Add class definition and new init
new_lines.append("class user_app_callback_class(app_callback_class):\n")
new_lines.append("    def __init__(self):\n")
new_lines.append("        super().__init__()\n")
new_lines.append("        # Structure: {track_id: {'direction': str, 'label': str, 'last_frame': int}}\n")
new_lines.append("        self.track_history = {}\n")
new_lines.append("        self.fps_start_time = time.monotonic()\n")
new_lines.append("        # Set of track_ids that have moved from center to a side zone\n")
new_lines.append("        self.IDs_changed_zones = set()\n")
new_lines.append("        self.depth_count = 0\n\n")

# Add get_depth_stats from depth_lines
idx_on_depth_frame = 0
for i in range(idx_get_depth_stats, len(depth_lines)):
    if depth_lines[i].startswith("def on_depth_frame"):
        idx_on_depth_frame = i
        break

for i in range(idx_get_depth_stats, idx_on_depth_frame):
    new_lines.append(depth_lines[i])

# Add get_fps from v2_lines
idx_get_fps = 0
for i, line in enumerate(v2_lines):
    if line.strip().startswith("def get_fps"):
        idx_get_fps = i
        break

idx_on_depth_frame_v2 = 0
for i in range(idx_get_fps, len(v2_lines)):
    if v2_lines[i].startswith("def on_depth_frame"):
        idx_on_depth_frame_v2 = i
        break

for i in range(idx_get_fps, idx_on_depth_frame_v2):
    new_lines.append(v2_lines[i])

# Add on_depth_frame from depth_lines
idx_cv2_draw_det = 0
for i in range(idx_on_depth_frame, len(depth_lines)):
    if depth_lines[i].startswith("def cv2_draw_det"):
        idx_cv2_draw_det = i
        break

for i in range(idx_on_depth_frame, idx_cv2_draw_det):
    new_lines.append(depth_lines[i])

# Replace cv2_draw_det and on_det_frame with v2 versions
idx_cv2_draw_det_v2 = 0
for i, line in enumerate(v2_lines):
    if line.startswith("def cv2_draw_det"):
        idx_cv2_draw_det_v2 = i
        break

idx_gstreamer_class_v2 = 0
for i in range(idx_cv2_draw_det_v2, len(v2_lines)):
    if v2_lines[i].startswith("class GStreamerDualApp"):
        idx_gstreamer_class_v2 = i
        break

for i in range(idx_cv2_draw_det_v2, idx_gstreamer_class_v2):
    new_lines.append(v2_lines[i])

# Add the rest of depth_lines
idx_gstreamer_class = 0
for i in range(idx_cv2_draw_det, len(depth_lines)):
    if depth_lines[i].startswith("class GStreamerDualApp"):
        idx_gstreamer_class = i
        break

for i in range(idx_gstreamer_class, len(depth_lines)):
    new_lines.append(depth_lines[i])

with open('sv_dual_callback_withdepth.py', 'w') as f:
    f.writelines(new_lines)

