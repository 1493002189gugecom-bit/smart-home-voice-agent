using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

namespace SmartHome
{
    /// <summary>
    /// Machine-readable error the vision service returns instead of a payload.
    ///
    /// The service answers failures with
    /// <c>{"ok":false,"error_code":"snake_case","message":"..."}</c>. Keeping the
    /// code as a stable identifier (rather than the human text) lets the panel
    /// decide which honest state to show without string matching English prose.
    /// </summary>
    public sealed class VisionError
    {
        public string error_code;
        public string message;

        public VisionError()
        {
            error_code = "unknown";
            message = null;
        }

        public VisionError(string errorCode, string errorMessage)
        {
            error_code = string.IsNullOrEmpty(errorCode) ? "unknown" : errorCode;
            message = errorMessage;
        }

        public override string ToString()
        {
            return string.IsNullOrEmpty(message) ? error_code : error_code + ": " + message;
        }
    }

    /// <summary>
    /// One body track from <c>GET /results</c>.
    ///
    /// Coordinates are normalized to the camera frame, never to the preview
    /// widget: the widget can be any size, so the overlay must scale the same
    /// numbers the service produced.
    /// </summary>
    public sealed class VisionTrackDto
    {
        public string track_id;
        /// <summary>Normalized corners, ordered [x1, y1, x2, y2].</summary>
        public float[] bbox = new float[4];
        /// <summary>17 COCO keypoints flattened as x0,y0,c0,x1,y1,c1,...</summary>
        public float[] keypoints = new float[0];
        public int keypointCount;
        public float detection_confidence;
        public string person_id;
        public string identity_state;
        public bool has_face_similarity;
        public float face_similarity;
        public string pose;
        public float pose_confidence;
        public long observed_at_ms;
        public string track_state;

        public bool bbox_valid;
        public bool keypoints_valid;

        public VisionTrackDto()
        {
            identity_state = VisionIdentity.Unknown;
            pose = VisionPose.Unknown;
            track_state = VisionTrackStates.Unknown;
        }

        /// <summary>True when the service confirmed a known person for this track.</summary>
        public bool IsConfirmed
        {
            get { return identity_state == VisionIdentity.Confirmed && !string.IsNullOrEmpty(person_id); }
        }

        public float KeypointX(int index)
        {
            return keypoints[index * 3];
        }

        public float KeypointY(int index)
        {
            return keypoints[index * 3 + 1];
        }

        public float KeypointConfidence(int index)
        {
            return keypoints[index * 3 + 2];
        }
    }

    /// <summary>Stable shape of <c>GET /results</c>.</summary>
    public sealed class VisionSnapshotDto
    {
        public bool ok;
        public string session_id;
        public string mode;
        public string camera_id;
        public string camera_room_id;
        public long observed_at_ms;
        public string sync_state;
        public readonly List<VisionTrackDto> tracks = new List<VisionTrackDto>();
        public VisionError error;

        public VisionSnapshotDto()
        {
            mode = VisionMode.Idle;
            sync_state = VisionSync.Unknown;
        }
    }

    /// <summary>One entry of <c>GET /cameras</c>.</summary>
    public sealed class VisionCameraDto
    {
        public string kind;
        /// <summary>Device index ("0") or the full URL, matching the select body.</summary>
        public string source_id;
        /// <summary>Server-redacted label; complete video URLs are never echoed.</summary>
        public string label;
        public int width;
        public int height;

        public string DisplayLabel
        {
            get
            {
                if (!string.IsNullOrEmpty(label))
                {
                    return width > 0 && height > 0 ? label + "  " + width + "x" + height : label;
                }

                return kind + ":" + source_id;
            }
        }
    }

    /// <summary>Camera state reported by <c>GET /config</c>.</summary>
    public sealed class VisionConfigDto
    {
        public string camera_id;
        public string camera_room_id;
        public string mode;
        public string sync_state;
        public string model_state;
        public string camera_state;
        public string error_code;
        public string message;
        public bool loaded;
    }

    /// <summary>
    /// Progress of the registration session from <c>GET /registration</c>.
    ///
    /// The service reports snake_case step and state codes; the panel owns the
    /// Chinese prompt text so a new service step cannot break the UI.
    /// </summary>
    public sealed class VisionRegistrationDto
    {
        public bool active;
        public string person_id;
        public string step;
        public int step_index;
        public int step_count;
        public int accepted_samples;
        public int required_samples;
        public string state;
        public long updated_at_ms;
        public string quality_reason;
        public int confirmation_remaining_ms;
        public string message;
    }

    /// <summary>
    /// Raised when a vision response cannot be understood at all: not JSON, not
    /// an object, or neither a valid payload nor a valid error envelope.
    ///
    /// Transport failures stay transport failures; this type exists so the panel
    /// can tell "the service said no" from "the service said something unknown".
    /// </summary>
    public sealed class VisionFormatException : Exception
    {
        public VisionFormatException(string message) : base(message)
        {
        }
    }

    /// <summary>Allowed pose and identity strings, kept as constants so typos fail loudly.</summary>
    public static class VisionPose
    {
        public const string Standing = "standing";
        public const string Sitting = "sitting";
        public const string Lying = "lying";
        public const string SuspectedFall = "suspected_fall";
        public const string HandRaised = "hand_raised";
        public const string Unknown = "unknown";
    }

    public static class VisionIdentity
    {
        public const string Unknown = "unknown";
        public const string Candidate = "candidate";
        public const string Confirmed = "confirmed";
        public const string Held = "held";
        public const string Conflict = "conflict";
    }

    public static class VisionTrackStates
    {
        public const string Active = "active";
        public const string Lost = "lost";
        public const string Unknown = "unknown";
    }

    public static class VisionMode
    {
        public const string Idle = "idle";
        public const string Monitoring = "monitoring";
        public const string Registering = "registering";
        public const string Error = "error";
    }

    public static class VisionSync
    {
        public const string Synced = "synced";
        public const string Pending = "pending";
        public const string Disconnected = "disconnected";
        public const string NotMonitoring = "not_monitoring";
        public const string Unknown = "unknown";
    }

    /// <summary>Registration steps, in the order the service drives them.</summary>
    public static class VisionRegistrationStep
    {
        public const string Front = "front";
        public const string TurnLeft = "turn_left";
        public const string TurnRight = "turn_right";
        public const string LookUp = "look_up";
        public const string LookDown = "look_down";
        public const string Blink = "blink";
        public const string Unknown = "unknown";
    }

    /// <summary>
    /// Display text shared by the camera panel and the house overlay.
    ///
    /// The mapping lives here rather than in the panel so the panel never has to
    /// guess: an unrecognised value degrades to 未知 instead of leaking a raw
    /// English code onto the screen.
    /// </summary>
    public static class VisionText
    {
        public static string PoseLabel(string pose)
        {
            switch (pose)
            {
                case VisionPose.Standing: return "站立";
                case VisionPose.Sitting: return "坐下";
                case VisionPose.Lying: return "躺下";
                case VisionPose.SuspectedFall: return "疑似跌倒";
                case VisionPose.HandRaised: return "举手";
                default: return "未知姿态";
            }
        }

        public static string IdentityLabel(string identityState)
        {
            switch (identityState)
            {
                case VisionIdentity.Confirmed: return "已确认";
                case VisionIdentity.Candidate: return "识别中";
                case VisionIdentity.Held: return "保持识别";
                case VisionIdentity.Conflict: return "身份冲突";
                default: return "未知人物";
            }
        }

        public static string ModeLabel(string mode)
        {
            switch (mode)
            {
                case VisionMode.Monitoring: return "监控中";
                case VisionMode.Registering: return "注册中";
                case VisionMode.Error: return "故障";
                case VisionMode.Idle: return "空闲";
                default: return "未知模式";
            }
        }

        public static string SyncLabel(string syncState)
        {
            switch (syncState)
            {
                case VisionSync.Synced: return "已同步";
                case VisionSync.Pending: return "同步待完成";
                case VisionSync.Disconnected: return "未同步";
                case VisionSync.NotMonitoring: return "未监控";
                default: return "同步状态未知";
            }
        }

        public static string RoomLabel(string roomId)
        {
            switch (roomId)
            {
                case "living_room": return "客厅";
                case "bedroom": return "卧室";
                case "kitchen": return "厨房";
                default: return string.IsNullOrEmpty(roomId) ? "未选择房间" : roomId;
            }
        }

        public static string PersonLabel(string personId)
        {
            switch (personId)
            {
                case "dad": return "爸爸";
                case "mom": return "妈妈";
                case "child": return "孩子";
                default: return string.IsNullOrEmpty(personId) ? "未知人物" : personId;
            }
        }

        public static string RegistrationStepLabel(string step)
        {
            switch (step)
            {
                case VisionRegistrationStep.Front: return "请正视镜头";
                case VisionRegistrationStep.TurnLeft: return "请向左转头";
                case VisionRegistrationStep.TurnRight: return "请向右转头";
                case VisionRegistrationStep.LookUp: return "请抬头";
                case VisionRegistrationStep.LookDown: return "请低头";
                case VisionRegistrationStep.Blink: return "请眨眼";
                default: return "等待注册指令";
            }
        }

        /// <summary>
        /// Chinese text for a service quality code.
        ///
        /// Every code the design lists as an honest fault (multi-face, small
        /// face, poor quality, wrong action) gets an explicit sentence; unknown
        /// codes are shown verbatim so a new server code is never silently
        /// swallowed, and never invented into a success.
        /// </summary>
        public static string QualityReasonLabel(string reason)
        {
            switch (reason)
            {
                case null:
                case "":
                    return null;
                case "multiple_faces": return "画面中有多张脸，请只保留注册者";
                case "no_face": return "没有检测到人脸";
                case "face_too_small": return "脸部过小，请靠近摄像头";
                case "low_detection_confidence": return "人脸不清晰";
                case "blurred": return "画面模糊，请保持稳定";
                case "too_dark": return "画面过暗，请补充光线";
                case "too_bright": return "画面过亮，请减少逆光";
                case "landmarks_incomplete": return "面部关键点不完整，请正对镜头";
                case "occluded": return "面部被遮挡";
                case "wrong_action": return "动作不符合当前要求";
                case "not_blink": return "未检测到眨眼";
                case "body_ambiguous": return "无法确定身体范围";
                case "camera_interrupted": return "摄像头已中断";
                case "model_missing": return "视觉模型未加载";
                default: return reason;
            }
        }
    }

    /// <summary>
    /// Readers for the vision JSON, built on the existing dependency-free
    /// <see cref="MiniJson"/> scanner.
    ///
    /// Every accessor is null-safe and type-tolerant because this is the boundary
    /// with a separate process: a missing or null field must degrade to a visible
    /// unknown, never to a default that looks like real data, and a malformed
    /// payload must raise <see cref="VisionFormatException"/> rather than return
    /// a half-populated DTO.
    /// </summary>
    public static class VisionJson
    {
        public const int CocoKeypointCount = 17;

        /// <summary>Reads an error envelope, or null when the payload is not one.</summary>
        public static VisionError TryReadError(string json)
        {
            Dictionary<string, object> map = RootObject(json, false);
            if (map == null)
            {
                return null;
            }

            object okValue;
            if (map.TryGetValue("ok", out okValue) && okValue is bool && !(bool)okValue)
            {
                return new VisionError(GetString(map, "error_code", "unknown"), GetString(map, "message", null));
            }

            // A payload can fail without an explicit "ok" flag; treat a lone
            // error_code as the error it clearly is.
            if (!map.ContainsKey("ok") && map.ContainsKey("error_code"))
            {
                return new VisionError(GetString(map, "error_code", "unknown"), GetString(map, "message", null));
            }

            return null;
        }

        /// <summary>
        /// Parses <c>GET /results</c>.
        ///
        /// An error envelope is surfaced as <see cref="VisionFormatException"/>
        /// carrying the service's own text, so a caller that only has JSON can
        /// still show the real reason instead of a generic parse failure.
        /// </summary>
        public static VisionSnapshotDto ParseResults(string json)
        {
            Dictionary<string, object> map = RootObject(json, true);
            VisionError error = TryReadError(json);
            if (error != null)
            {
                throw new VisionFormatException("vision error: " + error.ToString());
            }

            var dto = new VisionSnapshotDto
            {
                ok = GetBool(map, "ok", true),
                session_id = GetString(map, "session_id", null),
                mode = NormalizeMode(GetString(map, "mode", null)),
                camera_id = GetString(map, "camera_id", null),
                camera_room_id = GetString(map, "camera_room_id", null),
                observed_at_ms = GetLong(map, "observed_at_ms", 0L),
                sync_state = NormalizeSync(GetString(map, "sync_state", null)),
                error = null,
            };

            string errorCode = GetString(map, "error_code", null);
            if (!string.IsNullOrEmpty(errorCode))
            {
                dto.error = new VisionError(errorCode, GetString(map, "message", null));
                dto.ok = false;
            }

            foreach (object entry in GetArray(map, "tracks"))
            {
                VisionTrackDto track = ReadTrack(entry as Dictionary<string, object>);
                if (track != null)
                {
                    dto.tracks.Add(track);
                }
            }

            return dto;
        }

        /// <summary>Parses <c>GET /cameras</c>. A null result means "no list was usable".</summary>
        public static List<VisionCameraDto> ParseCameras(string json)
        {
            Dictionary<string, object> map = RootObject(json, true);
            VisionError error = TryReadError(json);
            if (error != null)
            {
                throw new VisionFormatException("vision error: " + error.ToString());
            }

            Dictionary<string, object> nested = GetObject(map, "data");
            if (nested != null)
            {
                map = nested;
            }

            var result = new List<VisionCameraDto>();
            foreach (object entry in GetArray(map, "cameras"))
            {
                var item = entry as Dictionary<string, object>;
                if (item == null)
                {
                    continue;
                }

                var camera = new VisionCameraDto
                {
                    kind = NormalizeCameraKind(GetString(item, "kind", null)),
                    label = GetString(item, "label", null),
                    width = GetInt(item, "width", 0),
                    height = GetInt(item, "height", 0),
                };

                if (camera.kind == "url")
                {
                    camera.source_id = GetString(item, "url", null);
                }
                else
                {
                    camera.source_id = GetString(item, "device_id", null);
                }

                if (string.IsNullOrEmpty(camera.source_id))
                {
                    // Cannot be selected without its id, so do not offer it.
                    continue;
                }

                result.Add(camera);
            }

            return result;
        }

        /// <summary>Parses <c>GET /config</c>.</summary>
        public static VisionConfigDto ParseConfig(string json)
        {
            Dictionary<string, object> map = RootObject(json, true);
            VisionError error = TryReadError(json);
            if (error != null)
            {
                throw new VisionFormatException("vision error: " + error.ToString());
            }

            var dto = new VisionConfigDto
            {
                loaded = true,
                camera_id = GetString(map, "camera_id", null),
                camera_room_id = GetString(map, "camera_room_id", null),
                mode = NormalizeMode(GetString(map, "mode", null)),
                sync_state = NormalizeSync(GetString(map, "sync_state", null)),
                model_state = GetString(map, "model_state", null),
                camera_state = GetString(map, "camera_state", null),
                error_code = GetString(map, "error_code", null),
                message = GetString(map, "message", null),
            };

            // Tolerate the state living one level down, as /results does not:
            // /config is documented as "service, model and camera status" and may
            // group it. Reading both shapes keeps one contract change from
            // blanking the panel.
            var service = GetObject(map, "service");
            if (service != null)
            {
                dto.mode = NormalizeMode(GetString(service, "mode", dto.mode));
                dto.sync_state = NormalizeSync(GetString(service, "sync_state", dto.sync_state));
                dto.model_state = GetString(service, "model_state", dto.model_state);
                dto.camera_state = GetString(service, "camera_state", dto.camera_state);
            }

            return dto;
        }

        /// <summary>
        /// Parses <c>GET /registration</c>.
        ///
        /// Returns null when the service reports no session in any recognised
        /// shape, which is how the panel distinguishes "idle" from "in progress".
        /// </summary>
        public static VisionRegistrationDto ParseRegistration(string json)
        {
            Dictionary<string, object> map = RootObject(json, true);
            VisionError error = TryReadError(json);
            if (error != null)
            {
                throw new VisionFormatException("vision error: " + error.ToString());
            }

            // The session may be the root object or nested; accept both rather
            // than inventing a second parser for each shape.
            Dictionary<string, object> source = map;
            var nested = GetObject(map, "registration");
            if (nested != null)
            {
                source = nested;
            }

            bool active = GetBool(source, "active", nested == null && GetBool(map, "ok", false));
            string personId = GetString(source, "person_id", null);
            string state = GetString(source, "state", null);
            if (!active && string.IsNullOrEmpty(personId) && string.IsNullOrEmpty(state))
            {
                return null;
            }

            return new VisionRegistrationDto
            {
                active = active,
                person_id = personId,
                step = NormalizeRegistrationStep(GetString(source, "step", null)),
                step_index = GetInt(source, "step_index", 0),
                step_count = GetInt(source, "step_count", 0),
                accepted_samples = GetInt(source, "accepted_samples", 0),
                required_samples = GetInt(source, "required_samples", 0),
                state = string.IsNullOrEmpty(state) ? "unknown" : state,
                updated_at_ms = GetLong(source, "updated_at_ms", 0L),
                quality_reason = GetString(source, "quality_reason", null),
                confirmation_remaining_ms = GetInt(source, "confirmation_remaining_ms", 0),
                message = GetString(source, "message", null),
            };
        }

        /// <summary>Reads a short check answer, e.g. <c>GET /health</c>.</summary>
        public static string ReadStatusText(string json)
        {
            Dictionary<string, object> map = RootObject(json, false);
            if (map == null)
            {
                return "unknown";
            }

            string status = GetString(map, "status", null);
            if (!string.IsNullOrEmpty(status))
            {
                return status;
            }

            string state = GetString(map, "state", null);
            if (!string.IsNullOrEmpty(state))
            {
                return state;
            }

            return GetBool(map, "ok", false) ? "ok" : "unknown";
        }

        private static VisionTrackDto ReadTrack(Dictionary<string, object> map)
        {
            if (map == null)
            {
                return null;
            }

            var track = new VisionTrackDto
            {
                track_id = GetString(map, "track_id", null),
                detection_confidence = GetFloat(map, "detection_confidence", 0f),
                person_id = GetString(map, "person_id", null),
                identity_state = NormalizeIdentity(GetString(map, "identity_state", null)),
                pose = NormalizePose(GetString(map, "pose", null)),
                pose_confidence = GetFloat(map, "pose_confidence", 0f),
                observed_at_ms = GetLong(map, "observed_at_ms", 0L),
                track_state = NormalizeTrackState(GetString(map, "track_state", null)),
            };

            float similarity;
            if (TryGetFloat(map, "face_similarity", out similarity))
            {
                track.has_face_similarity = true;
                track.face_similarity = similarity;
            }

            float[] bbox = ReadFloatArray(map, "bbox");
            if (bbox != null && bbox.Length == 4)
            {
                track.bbox = bbox;
                track.bbox_valid = true;
            }

            float[] keypoints = ReadFlatArrays(map, "keypoints", 3);
            if (keypoints != null && keypoints.Length >= CocoKeypointCount * 3)
            {
                track.keypoints = keypoints;
                track.keypointCount = keypoints.Length / 3;
                track.keypoints_valid = true;
            }
            else if (keypoints != null)
            {
                // Partially reported skeletons still draw what exists; the
                // overlay skips index-out-of-range edges instead of guessing.
                track.keypoints = keypoints;
                track.keypointCount = keypoints.Length / 3;
                track.keypoints_valid = track.keypointCount > 0;
            }
            else
            {
                track.keypoints = new float[0];
                track.keypointCount = 0;
            }

            if (string.IsNullOrEmpty(track.track_id))
            {
                return null;
            }

            return track;
        }

        private static Dictionary<string, object> RootObject(string json, bool required)
        {
            object root = MiniJson.Deserialize(json);
            var map = root as Dictionary<string, object>;
            if (map == null)
            {
                if (required)
                {
                    throw new VisionFormatException("root is not a JSON object");
                }

                return null;
            }

            return map;
        }

        internal static IEnumerable<object> GetArray(Dictionary<string, object> map, string key)
        {
            object value;
            if (map != null && map.TryGetValue(key, out value))
            {
                var list = value as List<object>;
                if (list != null)
                {
                    return list;
                }
            }

            return EmptyList;
        }

        private static readonly List<object> EmptyList = new List<object>();

        internal static Dictionary<string, object> GetObject(Dictionary<string, object> map, string key)
        {
            object value;
            if (map != null && map.TryGetValue(key, out value))
            {
                return value as Dictionary<string, object>;
            }

            return null;
        }

        internal static string GetString(Dictionary<string, object> map, string key, string fallback)
        {
            object value;
            if (map != null && map.TryGetValue(key, out value) && value != null)
            {
                var text = value as string;
                if (text != null)
                {
                    return text;
                }

                return value.ToString();
            }

            return fallback;
        }

        internal static bool GetBool(Dictionary<string, object> map, string key, bool fallback)
        {
            object value;
            if (map != null && map.TryGetValue(key, out value) && value != null)
            {
                if (value is bool)
                {
                    return (bool)value;
                }

                bool parsed;
                if (bool.TryParse(value.ToString(), out parsed))
                {
                    return parsed;
                }
            }

            return fallback;
        }

        internal static int GetInt(Dictionary<string, object> map, string key, int fallback)
        {
            double value;
            if (TryGetNumber(map, key, out value))
            {
                return (int)value;
            }

            return fallback;
        }

        internal static long GetLong(Dictionary<string, object> map, string key, long fallback)
        {
            double value;
            if (TryGetNumber(map, key, out value))
            {
                return (long)value;
            }

            return fallback;
        }

        internal static float GetFloat(Dictionary<string, object> map, string key, float fallback)
        {
            float value;
            if (TryGetFloat(map, key, out value))
            {
                return value;
            }

            return fallback;
        }

        internal static bool TryGetFloat(Dictionary<string, object> map, string key, out float value)
        {
            value = 0f;
            double number;
            if (!TryGetNumber(map, key, out number))
            {
                return false;
            }

            value = (float)number;
            return true;
        }

        /// <summary>
        /// Numbers stay as doubles from <see cref="MiniJson"/>, but a well-meant
        /// string ("0.5") is still accepted so one service-side formatting change
        /// does not blank the panel.
        /// </summary>
        private static bool TryGetNumber(Dictionary<string, object> map, string key, out double value)
        {
            value = 0d;
            object raw;
            if (map == null || !map.TryGetValue(key, out raw) || raw == null)
            {
                return false;
            }

            if (raw is double)
            {
                value = (double)raw;
                return true;
            }

            if (raw is bool)
            {
                return false;
            }

            return double.TryParse(raw.ToString(), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out value);
        }

        private static float[] ReadFloatArray(Dictionary<string, object> map, string key)
        {
            object value;
            if (map == null || !map.TryGetValue(key, out value))
            {
                return null;
            }

            var list = value as List<object>;
            if (list == null)
            {
                return null;
            }

            var result = new float[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                double number;
                if (!TryConvert(list[i], out number))
                {
                    return null;
                }

                result[i] = (float)number;
            }

            return result;
        }

        /// <summary>
        /// Flattens an array of fixed-length arrays, e.g. 17 [x,y,c] triples into
        /// 51 floats. Entries that are already flat numbers are also accepted, so
        /// a service that sends one flat list still works.
        /// </summary>
        private static float[] ReadFlatArrays(Dictionary<string, object> map, string key, int stride)
        {
            object value;
            if (map == null || !map.TryGetValue(key, out value))
            {
                return null;
            }

            var list = value as List<object>;
            if (list == null)
            {
                return null;
            }

            var result = new List<float>(list.Count * stride);
            for (int i = 0; i < list.Count; i++)
            {
                var nested = list[i] as List<object>;
                if (nested != null)
                {
                    for (int j = 0; j < nested.Count; j++)
                    {
                        double number;
                        if (!TryConvert(nested[j], out number))
                        {
                            return null;
                        }

                        result.Add((float)number);
                    }

                    continue;
                }

                double flat;
                if (!TryConvert(list[i], out flat))
                {
                    return null;
                }

                result.Add((float)flat);
            }

            return result.ToArray();
        }

        private static bool TryConvert(object raw, out double value)
        {
            value = 0d;
            if (raw == null || raw is bool)
            {
                return false;
            }

            if (raw is double)
            {
                value = (double)raw;
                return true;
            }

            return double.TryParse(raw.ToString(), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out value);
        }

        /// <summary>
        /// Unknown enum strings map to "unknown" instead of throwing: the service
        /// may add a pose or identity state long before this payload is updated,
        /// and showing 未知 is honest while a crash is not.
        /// </summary>
        public static string NormalizePose(string value)
        {
            switch (value)
            {
                case VisionPose.Standing:
                case VisionPose.Sitting:
                case VisionPose.Lying:
                case VisionPose.SuspectedFall:
                case VisionPose.HandRaised:
                case VisionPose.Unknown:
                    return value;
                default:
                    return VisionPose.Unknown;
            }
        }

        public static string NormalizeIdentity(string value)
        {
            switch (value)
            {
                case VisionIdentity.Unknown:
                case VisionIdentity.Candidate:
                case VisionIdentity.Confirmed:
                case VisionIdentity.Held:
                case VisionIdentity.Conflict:
                    return value;
                default:
                    return VisionIdentity.Unknown;
            }
        }

        public static string NormalizeTrackState(string value)
        {
            switch (value)
            {
                case VisionTrackStates.Active:
                case VisionTrackStates.Lost:
                    return value;
                default:
                    return VisionTrackStates.Unknown;
            }
        }

        public static string NormalizeMode(string value)
        {
            switch (value)
            {
                case VisionMode.Idle:
                case VisionMode.Monitoring:
                case VisionMode.Registering:
                case VisionMode.Error:
                    return value;
                default:
                    return VisionMode.Idle;
            }
        }

        public static string NormalizeSync(string value)
        {
            switch (value)
            {
                case VisionSync.Synced:
                case VisionSync.Pending:
                case VisionSync.Disconnected:
                case VisionSync.NotMonitoring:
                    return value;
                default:
                    return VisionSync.Unknown;
            }
        }

        public static string NormalizeRegistrationStep(string value)
        {
            switch (value)
            {
                case VisionRegistrationStep.Front:
                case VisionRegistrationStep.TurnLeft:
                case VisionRegistrationStep.TurnRight:
                case VisionRegistrationStep.LookUp:
                case VisionRegistrationStep.LookDown:
                case VisionRegistrationStep.Blink:
                    return value;
                default:
                    return VisionRegistrationStep.Unknown;
            }
        }

        public static string NormalizeCameraKind(string value)
        {
            return value == "url" ? "url" : "device";
        }

        /// <summary>
        /// Escapes one string for embedding in a JSON body.
        ///
        /// The camera URL is free text typed by the user. Concatenating it raw
        /// would let a quote or backslash break out of the string and craft extra
        /// JSON fields, so every value that reaches a request body goes through
        /// this single helper.
        /// </summary>
        public static string EscapeString(string value)
        {
            if (value == null)
            {
                return string.Empty;
            }

            var builder = new StringBuilder(value.Length + 8);
            for (int i = 0; i < value.Length; i++)
            {
                char c = value[i];
                switch (c)
                {
                    case '"': builder.Append("\\\""); break;
                    case '\\': builder.Append("\\\\"); break;
                    case '\b': builder.Append("\\b"); break;
                    case '\f': builder.Append("\\f"); break;
                    case '\n': builder.Append("\\n"); break;
                    case '\r': builder.Append("\\r"); break;
                    case '\t': builder.Append("\\t"); break;
                    default:
                        if (c < ' ')
                        {
                            builder.Append("\\u").Append(((int)c).ToString("x4"));
                        }
                        else if (char.IsSurrogate(c))
                        {
                            // Lone surrogates cannot survive UTF-8 encoding; emit
                            // the replacement character rather than invalid JSON.
                            builder.Append("\\ufffd");
                        }
                        else
                        {
                            builder.Append(c);
                        }

                        break;
                }
            }

            return builder.ToString();
        }

        /// <summary>
        /// Builds a flat JSON object from alternating key/value strings.
        ///
        /// Bodies are assembled locally rather than by string concatenation so
        /// user-typed values (a camera URL above all) always pass through
        /// <see cref="EscapeString"/>. A null value is written as JSON <c>null</c>
        /// so an absent field stays absent instead of becoming an empty string.
        /// </summary>
        public static string JsonObject(params string[] keysAndValues)
        {
            if (keysAndValues == null || keysAndValues.Length == 0)
            {
                return "{}";
            }

            if (keysAndValues.Length % 2 != 0)
            {
                throw new ArgumentException("JsonObject needs an even number of key/value strings", "keysAndValues");
            }

            var builder = new StringBuilder();
            builder.Append('{');
            for (int i = 0; i < keysAndValues.Length; i += 2)
            {
                if (i > 0)
                {
                    builder.Append(',');
                }

                builder.Append('"').Append(EscapeString(keysAndValues[i])).Append("\":");
                string value = keysAndValues[i + 1];
                if (value == null)
                {
                    builder.Append("null");
                }
                else
                {
                    builder.Append('"').Append(EscapeString(value)).Append('"');
                }
            }

            builder.Append('}');
            return builder.ToString();
        }
    }
}
