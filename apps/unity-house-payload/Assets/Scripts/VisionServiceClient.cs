using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace SmartHome
{
    /// <summary>
    /// Talks to the loopback vision service on 127.0.0.1:8766.
    ///
    /// This is a second, separate service from the home-service on 8765: home
    /// state still arrives through <see cref="HomeServiceClient"/>, while this
    /// client owns camera selection, mode changes, registration and the preview.
    /// It is deliberately non-blocking and bounded:
    ///
    /// * at most one <c>/results</c>, one <c>/registration</c> and one
    ///   <c>/preview.jpg</c> request are ever in flight, so a slow service cannot
    ///   build an unbounded queue of requests;
    /// * the preview is fetched only while the panel is visible, because the
    ///   JPEG is the expensive response and nobody can see it when hidden;
    /// * every request is disposed, every downloaded texture replaces and
    ///   destroys the previous one, and every callback arrives on Unity's main
    ///   thread because all of it runs inside coroutines.
    ///
    /// The client never invents state: a failed read clears the snapshot and the
    /// preview and publishes a disconnected status, which is what lets the panel
    /// show an honest fault instead of a stale picture.
    /// </summary>
    public sealed class VisionServiceClient : MonoBehaviour
    {
        [Tooltip("Loopback address of the vision service. LAN addresses are not used.")]
        public string baseUrl = "http://127.0.0.1:8766";

        [Tooltip("Seconds between /results and /registration polls.")]
        public float pollIntervalSeconds = 1f;

        [Tooltip("Seconds between reconnect attempts after a failure.")]
        public float reconnectDelaySeconds = 3f;

        [Tooltip("Seconds between preview frames while the panel is visible.")]
        public float previewIntervalSeconds = 0.5f;

        [Tooltip("Per-request timeout in seconds.")]
        public int requestTimeoutSeconds = 5;

        public event Action<VisionSnapshotDto> SnapshotReceived;
        public event Action<VisionRegistrationDto> RegistrationUpdated;
        public event Action<VisionConfigDto> ConfigReceived;
        public event Action<List<VisionCameraDto>> CamerasUpdated;
        public event Action<Texture2D> PreviewUpdated;
        public event Action<string> StatusChanged;
        public event Action<string> ErrorOccurred;
        public event Action<string> CommandCompleted;

        /// <summary>True once a <c>/results</c> read has succeeded.</summary>
        public bool Connected { get; private set; }

        /// <summary>Latest status line; always non-empty after the first poll.</summary>
        public string Status { get; private set; }

        public VisionSnapshotDto Snapshot { get; private set; }
        public VisionRegistrationDto Registration { get; private set; }
        public VisionConfigDto Config { get; private set; }
        public readonly List<VisionCameraDto> Cameras = new List<VisionCameraDto>();

        /// <summary>Current preview texture, or null while disconnected or hidden.</summary>
        public Texture2D PreviewTexture { get; private set; }

        /// <summary>True while any command request is outstanding.</summary>
        public bool CommandInFlight
        {
            get { return _commandCount > 0; }
        }

        /// <summary>
        /// True while a command that changes the session is outstanding.
        ///
        /// The panel disables camera and room controls on this flag: the service
        /// tears the old session down before starting the new one, so a second
        /// change in the middle of a transition would race it.
        /// </summary>
        public bool TransitionInFlight
        {
            get { return _transitionCount > 0; }
        }

        /// <summary>True while the panel is visible and the preview is fetching.</summary>
        public bool PreviewVisible { get; private set; }

        private Coroutine _pollRoutine;
        private Coroutine _commandRoutine;
        private Coroutine _previewRoutine;

        private bool _resultsInFlight;
        private bool _registrationInFlight;
        private bool _configInFlight;
        private bool _camerasInFlight;

        private int _commandCount;
        private int _transitionCount;

        /// <summary>
        /// Re-probes the physical cameras.
        ///
        /// Enumeration opens and releases real devices, so it happens when the
        /// user asks rather than on every poll.
        /// </summary>
        public void RequestCamerasRefresh()
        {
            if (!isActiveAndEnabled)
            {
                return;
            }

            StartCoroutine(FetchCameras());
        }

        public void StartPolling()
        {
            if (_pollRoutine != null)
            {
                StopCoroutine(_pollRoutine);
            }

            _pollRoutine = StartCoroutine(PollLoop());
        }

        public void StopPolling()
        {
            if (_pollRoutine != null)
            {
                StopCoroutine(_pollRoutine);
                _pollRoutine = null;
            }

            Connected = false;
            Snapshot = null;
            _resultsInFlight = false;
            _registrationInFlight = false;
            _configInFlight = false;
            _camerasInFlight = false;
            SetPreview(null);
            PublishStatus("未连接：视觉服务轮询已停止");
        }

        /// <summary>Stops polling and releases the preview texture.</summary>
        public void Dispose()
        {
            StopPolling();
        }

        /// <summary>
        /// Requests an extra <c>/results</c> read right now.
        ///
        /// Used after a command, where waiting up to a full poll interval would
        /// leave the panel showing the state the user just changed away from. The
        /// in-flight guard inside <see cref="FetchResults"/> keeps this from
        /// opening a second concurrent request.
        /// </summary>
        public void RequestResultsRefresh()
        {
            if (!isActiveAndEnabled)
            {
                return;
            }

            StartCoroutine(FetchResults());
        }

        /// <summary>
        /// Enables or disables preview fetching.
        ///
        /// Hiding the panel releases the texture immediately: keeping the last
        /// frame around would show a picture that may no longer correspond to the
        /// current camera or session.
        /// </summary>
        public void SetPreviewVisible(bool visible)
        {
            PreviewVisible = visible;
            if (_previewRoutine != null)
            {
                StopCoroutine(_previewRoutine);
                _previewRoutine = null;
            }

            if (!visible)
            {
                SetPreview(null);
                return;
            }

            _previewRoutine = StartCoroutine(PreviewLoop());
        }

        private void OnDisable()
        {
            if (_previewRoutine != null)
            {
                StopCoroutine(_previewRoutine);
                _previewRoutine = null;
            }

            if (_pollRoutine != null)
            {
                StopCoroutine(_pollRoutine);
                _pollRoutine = null;
            }

            SetPreview(null);
        }

        private void OnDestroy()
        {
            // The scratch texture is owned by this component, so it is released
            // with it rather than lingering as an orphaned GPU allocation.
            if (_previewScratch != null)
            {
                Destroy(_previewScratch);
                _previewScratch = null;
            }

            PreviewTexture = null;
            _ownedPreview = null;
        }

        private IEnumerator PollLoop()
        {
            // Camera enumeration opens each physical device briefly, so perform
            // it once on connection and thereafter only on explicit refresh.
            yield return FetchCameras();
            while (true)
            {
                yield return FetchConfig();
                yield return FetchResults();
                yield return FetchRegistration();

                float delay = Connected ? pollIntervalSeconds : reconnectDelaySeconds;
                yield return new WaitForSecondsRealtime(Mathf.Max(0.1f, delay));
            }
        }

        private IEnumerator PreviewLoop()
        {
            while (PreviewVisible)
            {
                if (!Connected)
                {
                    yield return new WaitForSecondsRealtime(Mathf.Max(0.1f, reconnectDelaySeconds));
                    continue;
                }
                // Small offset so the JPEG request does not always share a frame
                // with the JSON polls.
                yield return new WaitForSecondsRealtime(Mathf.Max(0.1f, previewIntervalSeconds));
                if (!PreviewVisible)
                {
                    yield break;
                }

                yield return FetchPreview();
            }
        }

        private IEnumerator FetchConfig()
        {
            if (_configInFlight)
            {
                yield break;
            }

            _configInFlight = true;
            VisionConfigDto parsed = null;
            string error = null;
            yield return GetJson("/config", value => parsed = VisionJson.ParseConfig(value), message => error = message);
            _configInFlight = false;

            if (parsed != null)
            {
                Config = parsed;
                ConfigReceived?.Invoke(parsed);
            }
            else if (error != null)
            {
                ReportError("/config: " + error);
            }
        }

        private IEnumerator FetchCameras()
        {
            if (_camerasInFlight)
            {
                yield break;
            }

            _camerasInFlight = true;
            List<VisionCameraDto> parsed = null;
            string error = null;
            yield return GetJson("/cameras", value => parsed = VisionJson.ParseCameras(value), message => error = message);
            _camerasInFlight = false;

            if (parsed != null)
            {
                Cameras.Clear();
                Cameras.AddRange(parsed);
                CamerasUpdated?.Invoke(parsed);
            }
            else if (error != null)
            {
                ReportError("/cameras: " + error);
            }
        }

        private IEnumerator FetchResults()
        {
            if (_resultsInFlight)
            {
                yield break;
            }

            _resultsInFlight = true;
            VisionSnapshotDto parsed = null;
            string error = null;
            yield return GetJson("/results", value => parsed = VisionJson.ParseResults(value), message => error = message);
            _resultsInFlight = false;

            if (parsed == null)
            {
                SetDisconnected("/results: " + (error ?? "unknown failure"));
                yield break;
            }

            SetConnected();
            Snapshot = parsed;
            SnapshotReceived?.Invoke(parsed);
        }

        private IEnumerator FetchRegistration()
        {
            if (_registrationInFlight)
            {
                yield break;
            }

            _registrationInFlight = true;
            VisionRegistrationDto parsed = null;
            string error = null;
            bool answered = false;
            yield return GetJson(
                "/registration",
                value =>
                {
                    parsed = VisionJson.ParseRegistration(value);
                    answered = true;
                },
                message =>
                {
                    // A service that is still starting answers 404 here; that is
                    // "no session", not a fault worth shouting about.
                    error = message;
                });
            _registrationInFlight = false;

            if (!answered)
            {
                if (!string.IsNullOrEmpty(error) && error.StartsWith("not_registered", StringComparison.Ordinal))
                {
                    Registration = null;
                    RegistrationUpdated?.Invoke(null);
                    yield break;
                }
                if (error != null)
                {
                    ReportError("/registration: " + error);
                }

                yield break;
            }

            Registration = parsed;
            RegistrationUpdated?.Invoke(parsed);
        }

        private IEnumerator FetchPreview()
        {
            using (UnityWebRequest request = UnityWebRequest.Get(baseUrl + "/preview.jpg"))
            {
                request.timeout = requestTimeoutSeconds;
                request.downloadHandler = new DownloadHandlerBuffer();
                yield return request.SendWebRequest();

                if (IsFailure(request))
                {
                    // Clear the picture: a stale frame next to a disconnected
                    // status would claim the camera is still live.
                    SetPreview(null);
                    // The service intentionally returns 503 until monitoring or
                    // registration has produced a first frame. Results/config
                    // polling remains authoritative for real camera faults, so
                    // treating this expected empty-preview state as a global
                    // client error would make the panel flash twice per second.
                    if (request.responseCode == 503)
                    {
                        yield break;
                    }

                    ReportError("/preview.jpg: " + Describe(request));
                    yield break;
                }

                byte[] bytes = request.downloadHandler.data;
                Texture2D texture = TextureFromJpeg(bytes);
                if (texture == null)
                {
                    SetPreview(null);
                    ReportError("/preview.jpg: 预览数据无法解码");
                    yield break;
                }

                SetPreview(texture);
                PreviewUpdated?.Invoke(texture);
            }
        }

        /// <summary>
        /// Builds a texture from JPEG bytes.
        ///
        /// The dimensions are read from the JPEG headers first because
        /// <c>Texture2D.LoadImage</c> on a Texture2D created without a size cannot
        /// replace the texture object. That keeps every preview frame in the same
        /// texture, so the RawImage never has to be rebound and the old bytes are
        /// overwritten instead of accumulating.
        /// </summary>
        private Texture2D TextureFromJpeg(byte[] bytes)
        {
            int width;
            int height;
            if (bytes == null || bytes.Length < 4 || !TryReadJpegSize(bytes, out width, out height))
            {
                return null;
            }

            if (_previewScratch == null || _previewScratch.width != width || _previewScratch.height != height)
            {
                if (_previewScratch != null)
                {
                    SetPreview(null);
                    Destroy(_previewScratch);
                }

                // Created at the exact camera size so LoadImage only overwrites
                // existing pixels; the same texture is reused for every later
                // frame at that resolution.
                _previewScratch = new Texture2D(width, height, TextureFormat.RGBA32, false);
                _previewScratch.hideFlags = HideFlags.HideAndDontSave;
            }

            if (!_previewScratch.LoadImage(bytes, false))
            {
                return null;
            }

            return _previewScratch;
        }

        private Texture2D _previewScratch;

        /// <summary>
        /// Reads width/height from the JPEG SOF marker.
        ///
        /// Parsing the two numbers is cheaper and safer than decoding the image
        /// twice, and it lets an undecodable payload be rejected before any
        /// texture is allocated.
        /// </summary>
        private static bool TryReadJpegSize(byte[] bytes, out int width, out int height)
        {
            width = 0;
            height = 0;
            if (bytes.Length < 4 || bytes[0] != 0xFF || bytes[1] != 0xD8)
            {
                return false;
            }

            int index = 2;
            while (index + 3 < bytes.Length)
            {
                if (bytes[index] != 0xFF)
                {
                    index++;
                    continue;
                }

                byte marker = bytes[index + 1];
                index += 2;

                // Padding fill bytes are legal between segments.
                if (marker == 0xFF)
                {
                    index--;
                    continue;
                }

                // Standalone markers carry no length payload.
                if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD9))
                {
                    continue;
                }

                if (index + 1 >= bytes.Length)
                {
                    return false;
                }

                int segmentLength = (bytes[index] << 8) | bytes[index + 1];
                if (segmentLength < 2 || index + segmentLength > bytes.Length)
                {
                    return false;
                }

                bool isStartOfFrame = (marker >= 0xC0 && marker <= 0xCF) &&
                                      marker != 0xC4 && marker != 0xC8 && marker != 0xCC;
                if (isStartOfFrame)
                {
                    if (index + 6 >= bytes.Length)
                    {
                        return false;
                    }

                    height = (bytes[index + 3] << 8) | bytes[index + 4];
                    width = (bytes[index + 5] << 8) | bytes[index + 6];
                    return width > 0 && height > 0;
                }

                index += segmentLength;
            }

            return false;
        }

        private void SetPreview(Texture2D texture)
        {
            if (_ownedPreview != null && _ownedPreview != texture)
            {
                // The old texture is ours and no longer referenced; leaving it
                // alive would leak one texture per session change.
                Destroy(_ownedPreview);
            }

            _ownedPreview = texture;
            PreviewTexture = texture;
        }

        private Texture2D _ownedPreview;

        public IEnumerator SelectDevice(string deviceId)
        {
            int index;
            if (!int.TryParse(deviceId, out index) || index < 0)
            {
                ReportError("摄像头编号必须是大于等于 0 的整数");
                yield break;
            }

            string body = VisionJson.JsonObject("kind", "device", "device_id", index.ToString());
            yield return RunTransition("/camera/select", body, "select_device");
        }

        public IEnumerator SelectUrl(string url)
        {
            string trimmed = url == null ? string.Empty : url.Trim();
            if (trimmed.Length == 0)
            {
                ReportError("请输入摄像头视频地址");
                yield break;
            }

            string scheme = UriScheme(trimmed);
            if (scheme != "http" && scheme != "https" && scheme != "rtsp")
            {
                ReportError("摄像头地址必须以 http、https 或 rtsp 开头");
                yield break;
            }

            // The URL is user input: it is escaped here and never echoed back in
            // a status message or a log line.
            string body = VisionJson.JsonObject("kind", "url", "url", trimmed);
            yield return RunTransition("/camera/select", body, "select_url");
        }

        public IEnumerator SelectRoom(string roomId)
        {
            if (!IsKnownRoom(roomId))
            {
                ReportError("未知房间：" + roomId);
                yield break;
            }

            string body = VisionJson.JsonObject("room_id", roomId);
            yield return RunTransition("/room/select", body, "select_room");
        }

        public IEnumerator StartMonitoring()
        {
            yield return RunTransition("/monitor/start", "{}", "monitor_start");
        }

        public IEnumerator PauseMonitoring()
        {
            yield return RunTransition("/monitor/pause", "{}", "monitor_pause");
        }

        public IEnumerator StartRegistration(string personId)
        {
            if (!IsKnownPerson(personId))
            {
                ReportError("未知人物：" + personId);
                yield break;
            }

            string body = VisionJson.JsonObject("person_id", personId);
            yield return RunCommand("/registration/start", "POST", body, "registration_start");
        }

        public IEnumerator CancelRegistration()
        {
            yield return RunCommand("/registration/cancel", "POST", "{}", "registration_cancel");
        }

        public IEnumerator DeleteRegistration(string personId)
        {
            if (!IsKnownPerson(personId))
            {
                ReportError("未知人物：" + personId);
                yield break;
            }

            yield return RunCommand("/registration/" + personId, "DELETE", null, "registration_delete");
        }

        /// <summary>Rooms the service accepts; anything else is a bug upstream.</summary>
        public static bool IsKnownRoom(string roomId)
        {
            return roomId == "living_room" || roomId == "bedroom" || roomId == "kitchen";
        }

        public static bool IsKnownPerson(string personId)
        {
            return personId == "dad" || personId == "mom" || personId == "child";
        }

        private static string UriScheme(string url)
        {
            int separator = url.IndexOf("://", StringComparison.Ordinal);
            if (separator <= 0)
            {
                return string.Empty;
            }

            return url.Substring(0, separator).ToLowerInvariant();
        }

        private IEnumerator RunTransition(string path, string body, string label)
        {
            yield return RunCommand(path, "POST", body, label);
        }

        /// <summary>
        /// Runs one command and reports its outcome.
        ///
        /// Commands wait for any earlier command to finish rather than running in
        /// parallel, an error envelope is surfaced verbatim, and success is only
        /// reported when the service actually said so — the panel must never show
        /// a state the service did not confirm.
        /// </summary>
        private IEnumerator RunCommand(string path, string method, string body, string label)
        {
            while (_commandRoutine != null)
            {
                yield return null;
            }

            _commandRoutine = StartCoroutine(CommandRequest(path, method, body, label));
            yield return _commandRoutine;
            _commandRoutine = null;
        }

        private IEnumerator CommandRequest(string path, string method, string body, string label)
        {
            bool transition = label == "select_device" || label == "select_url" ||
                              label == "select_room" || label == "monitor_start" || label == "monitor_pause";
            _commandCount++;
            if (transition)
            {
                _transitionCount++;
            }

            VisionError error = null;
            string responseJson = null;

            yield return SendRequest(path, method, body, value => responseJson = value, value => error = value);

            _commandCount--;
            if (transition)
            {
                _transitionCount--;
            }

            if (error != null)
            {
                ErrorOccurred?.Invoke(label + " 失败：" + error.ToString());
                CommandCompleted?.Invoke(label + ":error:" + error.error_code);
                yield break;
            }

            // The response may itself carry an error envelope with a 200 status,
            // so it is checked before the command is treated as accepted.
            VisionError envelope = string.IsNullOrEmpty(responseJson) ? null : VisionJson.TryReadError(responseJson);
            if (envelope != null)
            {
                ErrorOccurred?.Invoke(label + " 失败：" + envelope.ToString());
                CommandCompleted?.Invoke(label + ":error:" + envelope.error_code);
                yield break;
            }

            // A source or room change invalidates every previous observation, so
            // the client drops its cached snapshot and preview at once instead of
            // waiting for the next poll to correct them.
            if (transition)
            {
                Snapshot = null;
                SetPreview(null);
                SnapshotReceived?.Invoke(null);
                PreviewUpdated?.Invoke(null);
            }

            CommandCompleted?.Invoke(label + ":ok");
        }

        private IEnumerator GetJson(string path, Action<string> onParsed, Action<string> onFailed)
        {
            string json = null;
            VisionError error = null;
            yield return SendRequest(path, "GET", null, value => json = value, value => error = value);

            if (error != null)
            {
                onFailed(error.error_code + " " + (error.message ?? string.Empty));
                yield break;
            }

            try
            {
                onParsed(json);
            }
            catch (Exception ex)
            {
                onFailed(ex.Message);
            }
        }

        /// <summary>
        /// Sends one request and normalises transport failures and error
        /// envelopes into a <see cref="VisionError"/>. The request is always
        /// disposed, even when the caller's parsing throws afterwards.
        /// </summary>
        private IEnumerator SendRequest(string path, string method, string body, Action<string> onSuccess,
            Action<VisionError> onFailure)
        {
            string url = baseUrl + path;
            using (UnityWebRequest request = new UnityWebRequest(url, method))
            {
                request.downloadHandler = new DownloadHandlerBuffer();
                if (body != null)
                {
                    request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
                    request.SetRequestHeader("Content-Type", "application/json");
                }

                request.timeout = requestTimeoutSeconds;
                yield return request.SendWebRequest();

                if (IsFailure(request))
                {
                    string failedText = request.downloadHandler == null ? null : request.downloadHandler.text;
                    VisionError failedEnvelope = string.IsNullOrEmpty(failedText)
                        ? null
                        : VisionJson.TryReadError(failedText);
                    onFailure(failedEnvelope ?? new VisionError("transport_error", Describe(request)));
                    yield break;
                }

                string text = request.downloadHandler == null ? null : request.downloadHandler.text;
                VisionError envelope = string.IsNullOrEmpty(text) ? null : VisionJson.TryReadError(text);
                if (envelope != null)
                {
                    onFailure(envelope);
                    yield break;
                }

                onSuccess(text);
            }
        }

        private static bool IsFailure(UnityWebRequest request)
        {
#if UNITY_2020_1_OR_NEWER
            return request.result != UnityWebRequest.Result.Success;
#else
            return request.isNetworkError || request.isHttpError;
#endif
        }

        /// <summary>Short, non-echoing description of a failed request.</summary>
        private static string Describe(UnityWebRequest request)
        {
            string error = request.error;
            if (string.IsNullOrEmpty(error))
            {
                error = "response code " + request.responseCode;
            }

            return error;
        }

        private void SetConnected()
        {
            if (!Connected)
            {
                Connected = true;
                PublishStatus("已连接视觉服务");
            }
        }

        /// <summary>
        /// Drops every cached value and publishes a disconnected status.
        ///
        /// Cached tracks and preview frames are cleared here so a fault can never
        /// leave the overlay describing a session that no longer exists.
        /// </summary>
        private void SetDisconnected(string reason)
        {
            bool wasConnected = Connected;
            Connected = false;
            Snapshot = null;
            Registration = null;
            Config = null;
            Cameras.Clear();
            SetPreview(null);
            SnapshotReceived?.Invoke(null);
            PreviewUpdated?.Invoke(null);
            if (wasConnected || Status == null)
            {
                PublishStatus("未连接：无法读取视觉服务（" + reason + "）");
            }
        }

        private void ReportError(string message)
        {
            ErrorOccurred?.Invoke(message);
        }

        private void PublishStatus(string status)
        {
            Status = status;
            StatusChanged?.Invoke(status);
        }
    }
}
