using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace SmartHome
{
    /// <summary>
    /// Runtime-built uGUI camera panel for the local vision service.
    ///
    /// The payload intentionally ships no serialized prefabs or scenes: those are
    /// Unity-owned files, and a hand-written copy would be silently patched by the
    /// editor. This component therefore builds its whole hierarchy in code, which
    /// also means the panel works immediately after the script is added to a
    /// GameObject in an otherwise empty scene.
    ///
    /// The panel is a view, exactly like the rest of the payload. It shows what
    /// the vision service reports and never assumes a command succeeded: camera
    /// and room controls are locked during a transition, and the service's own
    /// error text is displayed instead of a hopeful status.
    /// </summary>
    public sealed class VisionCameraPanel : MonoBehaviour
    {
        [Tooltip("Vision service client that feeds this panel. Created on this GameObject when empty.")]
        public VisionServiceClient client;

        [Tooltip("Preview overlay. Created over the preview image when empty.")]
        public VisionOverlayGraphic overlay;

        [Tooltip("Width of the panel inside the canvas reference resolution.")]
        public float panelWidth = 620f;

        [Tooltip("Label colour of the last service error.")]
        public Color errorColor = new Color(1f, 0.45f, 0.45f, 1f);

        public Color normalColor = new Color(0.92f, 0.94f, 0.97f, 1f);
        public Color confirmedColor = new Color(0.35f, 0.9f, 0.5f, 1f);
        public Color secondaryColor = new Color(0.72f, 0.76f, 0.82f, 1f);

        private const int MaxTrackLabels = 16;
        private const float ConfirmWindowSeconds = 10f;

        private Canvas _canvas;
        private RectTransform _root;
        private RawImage _previewImage;
        private AspectRatioFitter _previewFitter;
        private Text _previewPlaceholder;
        private RectTransform _labelRoot;
        private Text _vitalsText;
        private Text _statusText;
        private Text _logText;
        private Text _actionText;
        private Text _progressText;
        private Text _qualityText;
        private Text _confirmStateText;
        private Text _dialogText;
        private Dropdown _sourceDropdown;
        private InputField _urlInput;
        private Button _selectUrlButton;
        private Dropdown _roomDropdown;
        private Button _refreshButton;
        private Button _monitorStartButton;
        private Button _monitorPauseButton;
        private Button _showPreviewButton;
        private readonly List<Button> _personButtons = new List<Button>();

        private readonly List<Text> _labelPool = new List<Text>();

        private readonly List<VisionCameraDto> _cameras = new List<VisionCameraDto>();
        private readonly List<VisionTrackDto> _lastTracks = new List<VisionTrackDto>();
        private string _lastPreviewSession;
        private string _lastCameraSignature;

        private bool _previewRequested;
        private bool _previewAvailable;
        private string _errorMessage;
        private string _commandMessage;
        private bool _built;

        private string _pendingConfirmAction;
        private string _pendingConfirmPerson;
        private float _pendingConfirmUntil;

        private static Font _font;

        private void Awake()
        {
            if (client == null)
            {
                client = GetComponent<VisionServiceClient>();
                if (client == null)
                {
                    client = gameObject.AddComponent<VisionServiceClient>();
                }
            }

            if (overlay == null)
            {
                overlay = GetComponentInChildren<VisionOverlayGraphic>(true);
            }
        }

        private void OnEnable()
        {
            if (!_built)
            {
                BuildHierarchy();
            }

            if (_canvas != null)
            {
                _canvas.enabled = true;
            }

            Subscribe();
            RefreshCameras(client == null ? null : client.Cameras);
            SetPreviewRequested(true);
            if (client != null)
            {
                client.StartPolling();
            }
        }

        private void OnDisable()
        {
            Unsubscribe();
            if (_canvas != null)
            {
                _canvas.enabled = false;
            }

            SetPreviewRequested(false);
            ClearTrackLabels();
            SetTracks(null);
            if (client != null)
            {
                client.StopPolling();
            }
        }

        private void OnDestroy()
        {
            Unsubscribe();
            ClearTrackLabels();
            if (_labelPool.Count > 0)
            {
                for (int i = 0; i < _labelPool.Count; i++)
                {
                    if (_labelPool[i] != null)
                    {
                        Destroy(_labelPool[i].gameObject);
                    }
                }

                _labelPool.Clear();
            }
        }

        private void Subscribe()
        {
            if (client == null)
            {
                return;
            }

            client.SnapshotReceived += OnSnapshot;
            client.RegistrationUpdated += OnRegistration;
            client.ConfigReceived += OnConfig;
            client.CamerasUpdated += OnCameras;
            client.PreviewUpdated += OnPreview;
            client.StatusChanged += OnClientStatus;
            client.ErrorOccurred += OnClientError;
            client.CommandCompleted += OnCommandCompleted;
        }

        private void Unsubscribe()
        {
            if (client == null)
            {
                return;
            }

            client.SnapshotReceived -= OnSnapshot;
            client.RegistrationUpdated -= OnRegistration;
            client.ConfigReceived -= OnConfig;
            client.CamerasUpdated -= OnCameras;
            client.PreviewUpdated -= OnPreview;
            client.StatusChanged -= OnClientStatus;
            client.ErrorOccurred -= OnClientError;
            client.CommandCompleted -= OnCommandCompleted;
        }

        // ------------------------------------------------------------------
        // Client events
        // ------------------------------------------------------------------

        private void OnSnapshot(VisionSnapshotDto snapshot)
        {
            if (snapshot == null)
            {
                // Disconnect or session change: nothing on screen may keep
                // describing the old session.
                SetTracks(null);
                _previewAvailable = false;
                UpdatePreviewImage();
                _vitalsText.text = client != null && client.Status != null ? client.Status : "未连接";
                _statusText.text = _errorMessage ?? "未连接：无法读取视觉服务，人物位置保持未知。";
                _statusText.color = errorColor;
                RefreshRegistrationViews(null);
                return;
            }

            string session = snapshot.session_id;
            if (!string.IsNullOrEmpty(_lastPreviewSession) && session != _lastPreviewSession)
            {
                // A new session means the old preview frame belongs to another
                // camera or room; it is dropped at once.
                _previewAvailable = false;
                UpdatePreviewImage();
            }

            _lastPreviewSession = session;
            _errorMessage = null;
            SetTracks(snapshot.tracks);
            RefreshRegistrationViews(client == null ? null : client.Registration);
            _vitalsText.text = ComposeVitals(snapshot);
            _vitalsText.color = normalColor;
            _statusText.text = ComposeStatus(snapshot);
            _statusText.color = snapshot.error != null ? errorColor : normalColor;
            _commandMessage = null;
        }

        private void OnRegistration(VisionRegistrationDto registration)
        {
            RefreshRegistrationViews(registration);
        }

        private void OnConfig(VisionConfigDto config)
        {
            string configMessage = ComposeConfigMessage(config);
            if (!string.IsNullOrEmpty(configMessage))
            {
                _errorMessage = configMessage;
            }
        }

        private void OnCameras(List<VisionCameraDto> cameras)
        {
            RefreshCameras(cameras);
        }

        private void OnPreview(Texture2D texture)
        {
            _previewAvailable = texture != null;
            UpdatePreviewImage();
        }

        private void OnClientStatus(string status)
        {
            AppendLog(status);
            if (client != null && client.Connected)
            {
                // The next snapshot repaints the vitals line with real values, so
                // nothing is written here.
                return;
            }

            if (_vitalsText != null)
            {
                _vitalsText.text = status;
                _vitalsText.color = normalColor;
            }
        }

        private void OnClientError(string message)
        {
            _errorMessage = message;
            AppendLog(message);
            if (_statusText != null)
            {
                _statusText.text = message;
                _statusText.color = errorColor;
            }
        }

        private void OnCommandCompleted(string result)
        {
            if (result == null)
            {
                return;
            }

            string[] parts = result.Split(':');
            string label = parts.Length > 0 ? parts[0] : result;
            bool ok = parts.Length > 1 && parts[1] == "ok";

            if (ok)
            {
                switch (label)
                {
                    case "monitor_start": _commandMessage = "已请求开始监控"; break;
                    case "monitor_pause": _commandMessage = "已请求暂停监控"; break;
                    case "select_device":
                    case "select_url": _commandMessage = "已请求切换摄像头，旧轨迹将全部撤销"; break;
                    case "select_room": _commandMessage = "已请求切换房间，旧轨迹将全部撤销"; break;
                    case "registration_start": _commandMessage = "已开始注册"; break;
                    case "registration_cancel": _commandMessage = "已取消注册"; break;
                    case "registration_delete": _commandMessage = "已删除注册特征"; break;
                    default: _commandMessage = null; break;
                }
            }
            else
            {
                // The service refused; the panel clears the claim rather than
                // pretending the change happened.
                _commandMessage = null;
            }

            if (!string.IsNullOrEmpty(_commandMessage))
            {
                AppendLog(_commandMessage);
            }

            RefreshControlInteractivity();
            if (client != null)
            {
                client.RequestResultsRefresh();
            }
        }

        // ------------------------------------------------------------------
        // Data presentation
        // ------------------------------------------------------------------

        private string ComposeVitals(VisionSnapshotDto snapshot)
        {
            string mode = VisionText.ModeLabel(snapshot.mode);
            string sync = VisionText.SyncLabel(snapshot.sync_state);
            string camera = string.IsNullOrEmpty(snapshot.camera_id) ? "未选择摄像头" : snapshot.camera_id;
            string room = string.IsNullOrEmpty(snapshot.camera_room_id) ? "未选择房间" : VisionText.RoomLabel(snapshot.camera_room_id);
            string model = "模型状态未知";
            if (client != null && client.Config != null)
            {
                model = ComposeModelState(client.Config);
            }

            return "服务 " + mode + " · 同步 " + sync + "\n" +
                   "摄像头 " + camera + " · 房间 " + room + "\n" + model;
        }

        private static string ComposeModelState(VisionConfigDto config)
        {
            if (config == null)
            {
                return "模型状态未知";
            }

            string model = string.IsNullOrEmpty(config.model_state) ? "unknown" : config.model_state;
            string camera = string.IsNullOrEmpty(config.camera_state) ? "unknown" : config.camera_state;
            return "模型 " + ModelLabel(model) + " · 摄像头 " + CameraStateLabel(camera);
        }

        private static string ModelLabel(string modelState)
        {
            switch (modelState)
            {
                case "loaded":
                case "ready":
                    return "已加载";
                case "model_missing":
                    return "模型缺失";
                case "loading":
                    return "加载中";
                case "error":
                    return "推理异常";
                default:
                    return modelState;
            }
        }

        private static string CameraStateLabel(string cameraState)
        {
            switch (cameraState)
            {
                case "open":
                case "opened":
                case "streaming":
                    return "已打开";
                case "camera_open_failed":
                    return "打开失败";
                case "camera_disconnected":
                    return "已断开";
                case "closed":
                    return "未打开";
                default:
                    return cameraState;
            }
        }

        /// <summary>
        /// Builds the honest status sentence for the current snapshot.
        ///
        /// The order matters: a fault outranks a content hint, so a disconnected
        /// camera is never described as "no person in view".
        /// </summary>
        private string ComposeStatus(VisionSnapshotDto snapshot)
        {
            if (!string.IsNullOrEmpty(_commandMessage))
            {
                return _commandMessage;
            }

            if (snapshot.error != null)
            {
                return "服务故障：" + VisionText.QualityReasonLabel(snapshot.error.error_code) +
                       (string.IsNullOrEmpty(snapshot.error.message) ? string.Empty : "（" + snapshot.error.message + "）");
            }

            if (snapshot.sync_state == VisionSync.Disconnected)
            {
                return "未同步：本地可继续预览，但人物位置不能声称已更新。";
            }

            if (!string.IsNullOrEmpty(_errorMessage))
            {
                return _errorMessage;
            }

            VisionRegistrationDto registration = client == null ? null : client.Registration;
            if (registration != null && registration.active)
            {
                return ComposeRegistrationStatus(registration);
            }

            int tracks = snapshot.tracks.Count;
            if (tracks == 0)
            {
                return snapshot.mode == VisionMode.Monitoring
                    ? "画面中没有人。"
                    : "当前没有人体轨迹。";
            }

            int known = 0;
            int unknown = 0;
            int lost = 0;
            for (int i = 0; i < snapshot.tracks.Count; i++)
            {
                VisionTrackDto track = snapshot.tracks[i];
                if (track.IsConfirmed)
                {
                    known++;
                }
                else if (track.identity_state == VisionIdentity.Unknown || track.identity_state == VisionIdentity.Conflict)
                {
                    unknown++;
                }

                if (track.track_state == VisionTrackStates.Lost)
                {
                    lost++;
                }
            }

            string text = "轨迹 " + tracks + "：已知 " + known + " · 未知 " + unknown;
            if (lost > 0)
            {
                text += " · 丢失 " + lost;
            }

            if (snapshot.sync_state == VisionSync.Pending)
            {
                text += "\n同步待完成：本次观察尚未被 home-service 确认。";
            }
            else if (snapshot.sync_state == VisionSync.NotMonitoring)
            {
                text += "\n未监控：当前结果不更新人物位置。";
            }

            return text;
        }

        private string ComposeRegistrationStatus(VisionRegistrationDto registration)
        {
            string person = VisionText.PersonLabel(registration.person_id);
            return "正在为 " + person + " 采集：" + VisionText.RegistrationStepLabel(registration.step);
        }

        private string ComposeConfigMessage(VisionConfigDto config)
        {
            if (config == null)
            {
                return null;
            }

            if (!string.IsNullOrEmpty(config.error_code))
            {
                return "服务报告 " + config.error_code +
                       (string.IsNullOrEmpty(config.message) ? string.Empty : "：" + config.message);
            }

            if (config.model_state == "model_missing")
            {
                return "模型未加载：视觉服务无法推理，人物位置保持未知。";
            }

            if (config.camera_state == "camera_open_failed")
            {
                return "摄像头打开失败：请检查设备是否被其他程序占用。";
            }

            return null;
        }

        private void SetTracks(List<VisionTrackDto> tracks)
        {
            _lastTracks.Clear();
            if (tracks != null)
            {
                _lastTracks.AddRange(tracks);
            }

            if (overlay != null)
            {
                overlay.SetTracks(_lastTracks);
            }

            RefreshTrackLabels();
        }

        /// <summary>
        /// Places one label per track.
        ///
        /// Labels are pooled Text objects created once, never GameObjects per
        /// frame: uGUI layout is the expensive part of a HUD, and the design
        /// requires bounded per-frame work.
        /// </summary>
        private void RefreshTrackLabels()
        {
            if (_labelRoot == null)
            {
                return;
            }

            for (int i = 0; i < _lastTracks.Count && i < MaxTrackLabels; i++)
            {
                VisionTrackDto track = _lastTracks[i];
                if (!track.bbox_valid)
                {
                    continue;
                }

                Text label = GetLabel(i);
                if (label == null)
                {
                    continue;
                }

                label.text = ComposeTrackLabel(track);
                label.color = track.IsConfirmed ? confirmedColor : unknownColorFor(track);
                label.gameObject.SetActive(true);
            }

            for (int i = _lastTracks.Count; i < _labelPool.Count; i++)
            {
                if (_labelPool[i] != null)
                {
                    _labelPool[i].gameObject.SetActive(false);
                }
            }

            LayoutTrackLabels();
        }

        private Color unknownColorFor(VisionTrackDto track)
        {
            return track.identity_state == VisionIdentity.Candidate || track.identity_state == VisionIdentity.Held
                ? new Color(1f, 0.82f, 0.35f, 1f)
                : new Color(1f, 0.5f, 0.5f, 1f);
        }

        private static string ComposeTrackLabel(VisionTrackDto track)
        {
            string pose = VisionText.PoseLabel(track.pose);
            if (track.IsConfirmed)
            {
                return VisionText.PersonLabel(track.person_id) + " · " + pose +
                       "  " + track.pose_confidence.ToString("0.00");
            }

            if (track.identity_state == VisionIdentity.Conflict)
            {
                // A conflict must not be shown as a name; the design keeps the
                // weaker track unknown instead of guessing.
                return "身份冲突 · " + pose;
            }

            return "未知人物 · " + pose;
        }

        private void ClearTrackLabels()
        {
            for (int i = 0; i < _labelPool.Count; i++)
            {
                if (_labelPool[i] != null)
                {
                    _labelPool[i].gameObject.SetActive(false);
                }
            }
        }

        private Text GetLabel(int index)
        {
            while (_labelPool.Count <= index && _labelPool.Count < MaxTrackLabels)
            {
                Text label = MakeText(_labelRoot, "TrackLabel" + _labelPool.Count, 14, FontStyle.Normal,
                    TextAnchor.LowerCenter);
                label.rectTransform.anchorMin = new Vector2(0.5f, 0f);
                label.rectTransform.anchorMax = new Vector2(0.5f, 0f);
                label.rectTransform.pivot = new Vector2(0.5f, 1f);
                label.rectTransform.sizeDelta = new Vector2(260f, 40f);
                label.horizontalOverflow = HorizontalWrapMode.Overflow;
                label.verticalOverflow = VerticalWrapMode.Overflow;
                label.gameObject.SetActive(false);
                _labelPool.Add(label);
            }

            return index < _labelPool.Count ? _labelPool[index] : null;
        }

        /// <summary>
        /// Positions labels through the overlay's aspect-fit mapping, which is the
        /// same mapping the boxes use, so a label can never detach from its box.
        /// </summary>
        private void LayoutTrackLabels()
        {
            if (_labelRoot == null || overlay == null || overlay.rectTransform == null)
            {
                return;
            }

            // Both the label container and the overlay stretch to the same
            // preview area, so overlay-local == label-local; the conversion below
            // only accounts for the container's box-rect offset.
            Rect labelRectArea = _labelRoot.rect;

            for (int i = 0; i < _lastTracks.Count && i < _labelPool.Count; i++)
            {
                VisionTrackDto track = _lastTracks[i];
                Text label = _labelPool[i];
                if (label == null || !label.gameObject.activeSelf)
                {
                    continue;
                }

                Vector2 mapped;
                if (!overlay.TryMapNormalized(new Vector2(track.bbox[0], track.bbox[1]), out mapped))
                {
                    continue;
                }

                // Labels anchor at the container's bottom-centre. Convert the
                // shared local point to that anchor reference exactly once.
                float localX = mapped.x - labelRectArea.center.x;
                float localY = mapped.y - labelRectArea.yMin;
                label.rectTransform.anchoredPosition = new Vector2(localX, localY);
            }
        }

        private void UpdatePreviewImage()
        {
            if (_previewImage == null)
            {
                return;
            }

            Texture2D texture = client == null ? null : client.PreviewTexture;
            if (texture != null && _previewAvailable)
            {
                _previewImage.texture = texture;
                _previewImage.color = Color.white;
                if (_previewFitter != null)
                {
                    float aspect = texture.height > 0 ? (float)texture.width / texture.height : 16f / 9f;
                    _previewFitter.aspectRatio = aspect;
                }

                if (_previewPlaceholder != null)
                {
                    _previewPlaceholder.gameObject.SetActive(false);
                }
            }
            else
            {
                _previewImage.texture = null;
                if (_previewPlaceholder != null)
                {
                    _previewPlaceholder.gameObject.SetActive(true);
                    _previewPlaceholder.text = _errorMessage ?? "预览未连接";
                }
            }
        }

        // ------------------------------------------------------------------
        // Controls
        // ------------------------------------------------------------------

        private void RefreshCameras(List<VisionCameraDto> cameras)
        {
            _cameras.Clear();
            if (cameras != null)
            {
                _cameras.AddRange(cameras);
            }

            if (_sourceDropdown == null)
            {
                RefreshControlInteractivity();
                return;
            }

            var options = new List<Dropdown.OptionData>();
            for (int i = 0; i < _cameras.Count; i++)
            {
                options.Add(new Dropdown.OptionData(_cameras[i].DisplayLabel));
            }

            if (options.Count == 0)
            {
                options.Add(new Dropdown.OptionData("未发现摄像头"));
            }

            _sourceDropdown.options = options;
            _sourceDropdown.captionText.text = options[0].text;
            if (_cameras.Count > 0)
            {
                _sourceDropdown.value = 0;
                _sourceDropdown.RefreshShownValue();
            }

            string signature = string.Empty;
            for (int i = 0; i < _cameras.Count; i++)
            {
                signature += _cameras[i].kind + ":" + _cameras[i].source_id + ";";
            }

            if (signature != _lastCameraSignature)
            {
                // Logged only on an actual change: the list is re-read every poll
                // and an unconditional line would drown the log within minutes.
                _lastCameraSignature = signature;
                AppendLog("发现摄像头 " + _cameras.Count + " 个");
                if (_cameras.Count == 0)
                {
                    // Only device indices were probed; a phone URL can still be
                    // typed into the address field.
                    AppendLog("未发现本机摄像头，可直接填写手机视频地址。");
                }

                RefreshControlInteractivity();
            }
        }

        /// <summary>
        /// Appends one line to the on-screen log.
        ///
        /// The log is capped so a service that fails every second cannot grow the
        /// text (and with it the canvas) without bound.
        /// </summary>
        private void AppendLog(string message)
        {
            if (_logText == null || string.IsNullOrEmpty(message))
            {
                return;
            }

            string line = System.DateTime.Now.ToString("HH:mm:ss") + "  " + message;
            _logText.text = string.IsNullOrEmpty(_logText.text) ? line : line + "\n" + _logText.text;
            if (_logText.text.Length > 1200)
            {
                _logText.text = _logText.text.Substring(0, 1200);
            }
        }

        private void RefreshRegistrationViews(VisionRegistrationDto registration)
        {
            bool active = registration != null && registration.active;

            for (int i = 0; i < _personButtons.Count; i++)
            {
                if (_personButtons[i] != null)
                {
                    _personButtons[i].interactable = !active || registration.person_id == PersonIdForButton(i);
                }
            }

            if (_actionText != null)
            {
                _actionText.text = active
                    ? VisionText.RegistrationStepLabel(registration.step)
                    : "未在注册。选择人物开始注册。";
            }

            if (_progressText != null)
            {
                if (active)
                {
                    int stepCount = registration.step_count > 0 ? registration.step_count : 6;
                    int stepIndex = Mathf.Clamp(registration.step_index, 0, stepCount);
                    string required = registration.required_samples > 0
                        ? registration.accepted_samples + "/" + registration.required_samples
                        : registration.accepted_samples.ToString();
                    _progressText.text = "步骤 " + stepIndex + "/" + stepCount + " · 已采集 " + required;
                }
                else
                {
                    _progressText.text = "步骤 -/- · 已采集 0";
                }
            }

            if (_qualityText != null)
            {
                string reason = active ? VisionText.QualityReasonLabel(registration.quality_reason) : null;
                if (string.IsNullOrEmpty(reason) && active)
                {
                    reason = "画面质量合格后才会计入采集。";
                }

                _qualityText.text = reason ?? "多脸、脸太小、模糊、遮挡或动作错误都会暂停采集。";
                _qualityText.color = active && !string.IsNullOrEmpty(registration.quality_reason)
                    ? errorColor
                    : secondaryColor;
            }

            if (_confirmStateText != null)
            {
                if (active && registration.state == "confirming")
                {
                    _confirmStateText.text = "确认中：剩余 " +
                                             Mathf.Max(0, registration.confirmation_remaining_ms / 1000) +
                                             " 秒，请保持正对镜头。";
                    _confirmStateText.color = confirmedColor;
                }
                else if (active && registration.state == "confirmation_failed")
                {
                    _confirmStateText.text = "确认失败：新特征已保存，可重试或删除后重新注册。";
                    _confirmStateText.color = errorColor;
                }
                else if (active && !string.IsNullOrEmpty(registration.message))
                {
                    _confirmStateText.text = registration.message;
                    _confirmStateText.color = secondaryColor;
                }
                else if (!active && registration != null && !string.IsNullOrEmpty(registration.message))
                {
                    _confirmStateText.text = registration.message;
                    _confirmStateText.color = secondaryColor;
                }
                else
                {
                    _confirmStateText.text = active ? "请按提示完成六步动作。" : "未处于注册确认阶段。";
                    _confirmStateText.color = secondaryColor;
                }
            }
        }

        private void RefreshControlInteractivity()
        {
            bool transition = client != null && client.TransitionInFlight;
            bool busy = client != null && client.CommandInFlight;

            if (_sourceDropdown != null)
            {
                _sourceDropdown.interactable = !transition && _cameras.Count > 0;
            }

            if (_refreshButton != null)
            {
                _refreshButton.interactable = !busy;
            }

            if (_selectUrlButton != null)
            {
                _selectUrlButton.interactable = !transition;
            }

            if (_urlInput != null)
            {
                _urlInput.interactable = !transition;
            }

            if (_roomDropdown != null)
            {
                _roomDropdown.interactable = !transition;
            }

            if (_monitorStartButton != null)
            {
                _monitorStartButton.interactable = !busy;
            }

            if (_monitorPauseButton != null)
            {
                _monitorPauseButton.interactable = !busy;
            }
        }

        private void SetPreviewRequested(bool requested)
        {
            _previewRequested = requested;
            if (client != null)
            {
                client.SetPreviewVisible(requested);
            }

            if (_showPreviewButton != null)
            {
                Text label = _showPreviewButton.GetComponentInChildren<Text>();
                if (label != null)
                {
                    label.text = requested ? "隐藏预览" : "显示预览";
                }
            }

            if (!requested)
            {
                _previewAvailable = false;
                UpdatePreviewImage();
            }
        }

        private void OnSourceChanged(int index)
        {
            if (client == null)
            {
                return;
            }

            if (index < 0 || index >= _cameras.Count)
            {
                SetStatusError("请选择有效的摄像头来源。");
                return;
            }

            if (client.TransitionInFlight)
            {
                SetStatusError("正在切换摄像头或房间，请等待完成。");
                return;
            }

            VisionCameraDto camera = _cameras[index];
            StartCoroutine(camera.kind == "url" ? client.SelectUrl(camera.source_id) : client.SelectDevice(camera.source_id));
        }

        private void OnRoomChanged(int index)
        {
            if (client == null)
            {
                return;
            }

            if (client.TransitionInFlight)
            {
                SetStatusError("正在切换摄像头或房间，请等待完成。");
                return;
            }

            StartCoroutine(client.SelectRoom(RoomIdForIndex(index)));
        }

        private static string RoomIdForIndex(int index)
        {
            switch (index)
            {
                case 1: return "bedroom";
                case 2: return "kitchen";
                default: return "living_room";
            }
        }

        private static string PersonIdForButton(int index)
        {
            switch (index)
            {
                case 1: return "mom";
                case 2: return "child";
                default: return "dad";
            }
        }

        private void OnSelectUrlClicked()
        {
            if (client == null)
            {
                return;
            }

            if (client.TransitionInFlight)
            {
                SetStatusError("正在切换摄像头或房间，请等待完成。");
                return;
            }

            StartCoroutine(client.SelectUrl(_urlInput == null ? null : _urlInput.text));
        }

        private void OnRefreshClicked()
        {
            if (client == null)
            {
                return;
            }

            // Probing devices is an explicit action: it opens and releases every
            // candidate index, so it is never part of the normal poll.
            client.RequestCamerasRefresh();
        }

        private void OnMonitorStartClicked()
        {
            if (client == null)
            {
                return;
            }

            StartCoroutine(client.StartMonitoring());
        }

        private void OnMonitorPauseClicked()
        {
            if (client == null)
            {
                return;
            }

            StartCoroutine(client.PauseMonitoring());
        }

        private void OnShowPreviewClicked()
        {
            SetPreviewRequested(!_previewRequested);
        }

        private void OnPersonClicked(int index)
        {
            string personId = PersonIdForButton(index);
            if (_pendingConfirmAction == "delete" && _pendingConfirmPerson == personId)
            {
                ClearPendingConfirm();
                StartCoroutine(client.DeleteRegistration(personId));
                return;
            }

            if (_pendingConfirmAction == "register" && _pendingConfirmPerson == personId)
            {
                ClearPendingConfirm();
                StartCoroutine(client.StartRegistration(personId));
                return;
            }

            if (client == null)
            {
                return;
            }

            if (client.Registration != null && client.Registration.active)
            {
                SetStatusError("已有注册会话，请先取消再为其他人注册。");
                return;
            }

            StartCoroutine(client.StartRegistration(personId));
        }

        private void OnCancelRegistrationClicked()
        {
            if (client == null)
            {
                return;
            }

            ClearPendingConfirm();
            StartCoroutine(client.CancelRegistration());
        }

        private void OnDeleteClicked()
        {
            if (client == null)
            {
                return;
            }

            string person = _pendingConfirmPerson;
            if (string.IsNullOrEmpty(person))
            {
                VisionRegistrationDto registration = client.Registration;
                person = registration != null && !string.IsNullOrEmpty(registration.person_id) ? registration.person_id : "dad";
            }

            // The confirmation names the person: deleting the wrong embeddings is
            // destructive and cannot be undone.
            AskConfirm("delete", person, "确认删除 " + VisionText.PersonLabel(person) + " 的全部人脸特征？此操作不可恢复。");
        }

        private void OnReregisterClicked()
        {
            if (client == null)
            {
                return;
            }

            string person = _pendingConfirmPerson;
            if (string.IsNullOrEmpty(person))
            {
                VisionRegistrationDto registration = client.Registration;
                person = registration != null && !string.IsNullOrEmpty(registration.person_id) ? registration.person_id : "dad";
            }

            AskConfirm("register", person,
                "确认重新注册 " + VisionText.PersonLabel(person) + "？新特征会原子替换旧特征。");
        }

        private void AskConfirm(string action, string personId, string dialog)
        {
            _pendingConfirmAction = action;
            _pendingConfirmPerson = personId;
            _pendingConfirmUntil = Time.unscaledTime + ConfirmWindowSeconds;

            if (_dialogText != null)
            {
                _dialogText.text = dialog;
                _dialogText.color = errorColor;
                _dialogText.gameObject.SetActive(true);
            }

            for (int i = 0; i < _personButtons.Count; i++)
            {
                if (_personButtons[i] != null && PersonIdForButton(i) == personId)
                {
                    Text label = _personButtons[i].GetComponentInChildren<Text>();
                    if (label != null)
                    {
                        label.text = (action == "delete" ? "确认删除 " : "确认重注册 ") + VisionText.PersonLabel(personId) + "？";
                    }
                }
            }
        }

        private void ClearPendingConfirm()
        {
            string person = _pendingConfirmPerson;
            _pendingConfirmAction = null;
            _pendingConfirmPerson = null;
            _pendingConfirmUntil = 0f;

            if (_dialogText != null)
            {
                _dialogText.gameObject.SetActive(false);
            }

            for (int i = 0; i < _personButtons.Count; i++)
            {
                if (_personButtons[i] != null && PersonIdForButton(i) == person)
                {
                    Text label = _personButtons[i].GetComponentInChildren<Text>();
                    if (label != null)
                    {
                        label.text = VisionText.PersonLabel(PersonIdForButton(i));
                    }
                }
            }
        }

        private void Update()
        {
            if (_pendingConfirmAction != null && Time.unscaledTime > _pendingConfirmUntil)
            {
                // A confirmation left open forever would let a later unrelated
                // click delete someone's embeddings.
                ClearPendingConfirm();
            }
        }

        private void SetStatusError(string message)
        {
            _errorMessage = message;
            if (_statusText != null)
            {
                _statusText.text = message;
                _statusText.color = errorColor;
            }
        }

        // ------------------------------------------------------------------
        // Hierarchy construction
        // ------------------------------------------------------------------

        private void BuildHierarchy()
        {
            _built = true;

            if (overlay == null)
            {
                // No assigned panel: create one Screen Space Overlay canvas that
                // holds the whole camera UI.
                EnsureEventSystem();
                GameObject canvasGo = new GameObject("VisionCameraCanvas", typeof(RectTransform));
                canvasGo.transform.SetParent(transform, false);
                _canvas = canvasGo.AddComponent<Canvas>();
                _canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                _canvas.sortingOrder = 200;

                var scaler = canvasGo.AddComponent<CanvasScaler>();
                scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
                scaler.referenceResolution = new Vector2(1920f, 1080f);
                scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
                scaler.matchWidthOrHeight = 0.5f;

                canvasGo.AddComponent<GraphicRaycaster>();
                _root = canvasGo.GetComponent<RectTransform>();
            }
            else
            {
                _canvas = overlay.canvas;
                _root = _canvas == null ? null : _canvas.GetComponent<RectTransform>();
                if (_root == null)
                {
                    Debug.LogWarning("[vision] assigned overlay has no Canvas; the panel cannot lay itself out.");
                    return;
                }
            }

            GameObject leftGo = NewUiObject("LeftColumn", _root);
            SetFixedWidth(leftGo, -1f);
            var leftLayout = leftGo.AddComponent<VerticalLayoutGroup>();
            leftLayout.spacing = 8f;
            leftLayout.padding = new RectOffset(10, 10, 10, 10);
            leftLayout.childAlignment = TextAnchor.UpperLeft;
            leftLayout.childControlWidth = true;
            leftLayout.childControlHeight = true;
            leftLayout.childForceExpandWidth = true;
            leftLayout.childForceExpandHeight = false;

            GameObject rightGo = NewUiObject("RightColumn", _root);
            SetFixedWidth(rightGo, panelWidth);
            var rightLayout = rightGo.AddComponent<VerticalLayoutGroup>();
            rightLayout.spacing = 8f;
            rightLayout.padding = new RectOffset(10, 10, 10, 10);
            rightLayout.childAlignment = TextAnchor.UpperLeft;
            rightLayout.childControlWidth = true;
            rightLayout.childControlHeight = true;
            rightLayout.childForceExpandWidth = true;
            rightLayout.childForceExpandHeight = false;

            GameObject rootGo = _root.gameObject;
            var rootLayout = rootGo.GetComponent<HorizontalLayoutGroup>();
            if (rootLayout == null)
            {
                rootLayout = rootGo.AddComponent<HorizontalLayoutGroup>();
            }

            rootLayout.spacing = 8f;
            rootLayout.padding = new RectOffset(12, 12, 12, 12);
            rootLayout.childAlignment = TextAnchor.UpperLeft;
            rootLayout.childControlWidth = true;
            rootLayout.childControlHeight = true;
            rootLayout.childForceExpandWidth = true;
            rootLayout.childForceExpandHeight = true;

            BuildHeader(leftGo);
            BuildPreview(leftGo);
            BuildControls(rightGo);
            BuildRegistration(rightGo);
            BuildStatus(rightGo);
        }

        private void BuildHeader(GameObject parent)
        {
            GameObject card = NewCard(parent, "Header", new Color(0.12f, 0.14f, 0.18f, 0.95f));
            var layout = card.AddComponent<VerticalLayoutGroup>();
            ConfigureCardLayout(layout);
            SetFlexibleHeight(card, 0f, 0f);

            Text header = MakeText(card.transform, "Title", 26, FontStyle.Bold, TextAnchor.MiddleLeft);
            header.text = "本地视觉摄像头";
            SetFlexibleHeight(header.gameObject, 0f, 0f);
            SetPreferredHeight(header.gameObject, 34f);

            _vitalsText = MakeText(card.transform, "Vitals", 16, FontStyle.Normal, TextAnchor.UpperLeft);
            _vitalsText.text = "等待服务状态…";
            _vitalsText.color = normalColor;
            SetPreferredHeight(_vitalsText.gameObject, 62f);
        }

        private void BuildPreview(GameObject parent)
        {
            GameObject area = NewUiObject("PreviewArea", parent.transform);
            area.AddComponent<Image>().color = new Color(0.02f, 0.02f, 0.03f, 1f);
            SetFlexibleHeight(area, 1f, 1f);

            GameObject image = NewUiObject("Preview", area.transform);
            image.AddComponent<RawImage>();
            Stretch(image.GetComponent<RectTransform>(), 0f);
            _previewImage = image.GetComponent<RawImage>();
            _previewImage.color = Color.white;
            _previewImage.raycastTarget = false;

            // The fitter lives on the image itself: it uses its parent's rect as
            // the reference, so putting it on the parent would make the preview
            // resize the very rectangle it measures.
            _previewFitter = image.AddComponent<AspectRatioFitter>();
            _previewFitter.aspectMode = AspectRatioFitter.AspectMode.FitInParent;
            _previewFitter.aspectRatio = 16f / 9f;

            GameObject placeholder = NewUiObject("PreviewPlaceholder", area.transform);
            MakeText(placeholder.transform, "PlaceholderLabel", 18, FontStyle.Normal, TextAnchor.MiddleCenter);
            Stretch(placeholder.GetComponent<RectTransform>(), 0f);
            _previewPlaceholder = placeholder.GetComponentInChildren<Text>();
            _previewPlaceholder.text = "预览未连接";
            _previewPlaceholder.color = secondaryColor;
            _previewPlaceholder.raycastTarget = false;

            if (overlay == null)
            {
                GameObject overlayGo = NewUiObject("Overlay", area.transform);
                Stretch(overlayGo.GetComponent<RectTransform>(), 0f);
                overlayGo.AddComponent<CanvasRenderer>();
                overlay = overlayGo.AddComponent<VisionOverlayGraphic>();
                overlay.preview = _previewImage;
            }

            // Labels are children of the panel, not of the overlay graphic: text
            // is pooled here and only positioned through the overlay's mapping.
            GameObject labelRoot = NewUiObject("TrackLabels", area.transform);
            Stretch(labelRoot.GetComponent<RectTransform>(), 0f);
            _labelRoot = labelRoot.GetComponent<RectTransform>();
        }

        private void BuildControls(GameObject parent)
        {
            GameObject card = NewCard(parent, "Controls", new Color(0.12f, 0.14f, 0.18f, 0.95f));
            var layout = card.AddComponent<VerticalLayoutGroup>();
            ConfigureCardLayout(layout);
            SetFlexibleHeight(card, 0f, 0f);

            Text title = MakeText(card.transform, "Title", 20, FontStyle.Bold, TextAnchor.MiddleLeft);
            title.text = "摄像头与房间";
            SetPreferredHeight(title.gameObject, 26f);

            Text sourceLabel = MakeText(card.transform, "SourceLabel", 15, FontStyle.Normal, TextAnchor.MiddleLeft);
            sourceLabel.text = "物理摄像头来源";
            sourceLabel.color = secondaryColor;
            SetPreferredHeight(sourceLabel.gameObject, 22f);

            _sourceDropdown = MakeDropdown(card.transform, "SourceDropdown", 30f);
            _sourceDropdown.onValueChanged.AddListener(OnSourceChanged);

            GameObject sourceRow = MakeRow(card.transform, "SourceRow", 32f);
            _refreshButton = MakeButton(sourceRow.transform, "RefreshButton", "刷新列表", 16, new Color(0.22f, 0.26f, 0.33f, 1f));
            _refreshButton.onClick.AddListener(OnRefreshClicked);
            SetFlexibleHeight(_refreshButton.gameObject, 1f, 1f);

            Text urlLabel = MakeText(card.transform, "UrlLabel", 15, FontStyle.Normal, TextAnchor.MiddleLeft);
            urlLabel.text = "手机视频地址（http/https/rtsp）";
            urlLabel.color = secondaryColor;
            SetPreferredHeight(urlLabel.gameObject, 22f);

            _urlInput = MakeInputField(card.transform, "UrlInput", "rtsp://192.168.1.20:8554/cam", 30f);

            GameObject urlRow = MakeRow(card.transform, "UrlRow", 32f);
            _selectUrlButton = MakeButton(urlRow.transform, "SelectUrlButton", "使用该地址",
                16, new Color(0.22f, 0.3f, 0.42f, 1f));
            _selectUrlButton.onClick.AddListener(OnSelectUrlClicked);
            SetFlexibleHeight(_selectUrlButton.gameObject, 1f, 1f);

            Text roomLabel = MakeText(card.transform, "RoomLabel", 15, FontStyle.Normal, TextAnchor.MiddleLeft);
            roomLabel.text = "逻辑房间（同一摄像头只代表一个房间）";
            roomLabel.color = secondaryColor;
            SetPreferredHeight(roomLabel.gameObject, 22f);

            _roomDropdown = MakeDropdown(card.transform, "RoomDropdown", 30f);
            _roomDropdown.options = new List<Dropdown.OptionData>
            {
                new Dropdown.OptionData("客厅"),
                new Dropdown.OptionData("卧室"),
                new Dropdown.OptionData("厨房"),
            };
            _roomDropdown.captionText.text = "客厅";
            _roomDropdown.value = 0;
            _roomDropdown.RefreshShownValue();
            _roomDropdown.onValueChanged.AddListener(OnRoomChanged);

            GameObject monitorRow = MakeRow(card.transform, "MonitorRow", 34f);
            _monitorStartButton = MakeButton(monitorRow.transform, "MonitorStart", "开始监控",
                16, new Color(0.16f, 0.4f, 0.26f, 1f));
            _monitorStartButton.onClick.AddListener(OnMonitorStartClicked);
            SetFlexibleHeight(_monitorStartButton.gameObject, 1f, 1f);

            _monitorPauseButton = MakeButton(monitorRow.transform, "MonitorPause", "暂停监控",
                16, new Color(0.42f, 0.3f, 0.16f, 1f));
            _monitorPauseButton.onClick.AddListener(OnMonitorPauseClicked);
            SetFlexibleHeight(_monitorPauseButton.gameObject, 1f, 1f);

            _showPreviewButton = MakeButton(card.transform, "ShowPreview", "隐藏预览",
                16, new Color(0.24f, 0.24f, 0.3f, 1f));
            _showPreviewButton.onClick.AddListener(OnShowPreviewClicked);
            SetPreferredHeight(_showPreviewButton.gameObject, 30f);

            RefreshControlInteractivity();
        }

        private void BuildRegistration(GameObject parent)
        {
            GameObject card = NewCard(parent, "Registration", new Color(0.12f, 0.14f, 0.18f, 0.95f));
            var layout = card.AddComponent<VerticalLayoutGroup>();
            ConfigureCardLayout(layout);
            SetFlexibleHeight(card, 1f, 1f);

            Text title = MakeText(card.transform, "Title", 20, FontStyle.Bold, TextAnchor.MiddleLeft);
            title.text = "人脸注册";
            SetPreferredHeight(title.gameObject, 26f);

            GameObject personRow = MakeRow(card.transform, "PersonRow", 32f);
            string[] names = { "爸爸", "妈妈", "孩子" };
            for (int i = 0; i < names.Length; i++)
            {
                int index = i;
                Button button = MakeButton(personRow.transform, "Person" + names[i], names[i],
                    16, new Color(0.2f, 0.26f, 0.36f, 1f));
                button.onClick.AddListener(() => OnPersonClicked(index));
                SetFlexibleHeight(button.gameObject, 1f, 1f);
                _personButtons.Add(button);
            }

            _actionText = MakeText(card.transform, "Action", 22, FontStyle.Bold, TextAnchor.MiddleLeft);
            _actionText.text = "未在注册。选择人物开始注册。";
            SetPreferredHeight(_actionText.gameObject, 30f);

            _progressText = MakeText(card.transform, "Progress", 16, FontStyle.Normal, TextAnchor.MiddleLeft);
            _progressText.text = "步骤 -/- · 已采集 0";
            _progressText.color = normalColor;
            SetPreferredHeight(_progressText.gameObject, 24f);

            _qualityText = MakeText(card.transform, "Quality", 15, FontStyle.Normal, TextAnchor.UpperLeft);
            _qualityText.text = "多脸、脸太小、模糊、遮挡或动作错误都会暂停采集。";
            _qualityText.color = secondaryColor;
            SetPreferredHeight(_qualityText.gameObject, 44f);

            _confirmStateText = MakeText(card.transform, "ConfirmState", 15, FontStyle.Normal, TextAnchor.UpperLeft);
            _confirmStateText.text = "未处于注册确认阶段。";
            _confirmStateText.color = secondaryColor;
            SetPreferredHeight(_confirmStateText.gameObject, 44f);

            _dialogText = MakeText(card.transform, "Dialog", 15, FontStyle.Bold, TextAnchor.UpperLeft);
            _dialogText.color = errorColor;
            _dialogText.gameObject.SetActive(false);
            SetPreferredHeight(_dialogText.gameObject, 40f);

            GameObject actionRow = MakeRow(card.transform, "RegistrationActions", 32f);
            Button cancel = MakeButton(actionRow.transform, "CancelButton", "取消注册",
                15, new Color(0.3f, 0.26f, 0.2f, 1f));
            cancel.onClick.AddListener(OnCancelRegistrationClicked);
            SetFlexibleHeight(cancel.gameObject, 1f, 1f);

            Button delete = MakeButton(actionRow.transform, "DeleteButton", "删除注册",
                15, new Color(0.42f, 0.2f, 0.2f, 1f));
            delete.onClick.AddListener(OnDeleteClicked);
            SetFlexibleHeight(delete.gameObject, 1f, 1f);

            Button reregister = MakeButton(actionRow.transform, "ReregisterButton", "重新注册",
                15, new Color(0.24f, 0.3f, 0.42f, 1f));
            reregister.onClick.AddListener(OnReregisterClicked);
            SetFlexibleHeight(reregister.gameObject, 1f, 1f);
        }

        private void BuildStatus(GameObject parent)
        {
            GameObject card = NewCard(parent, "Status", new Color(0.1f, 0.12f, 0.16f, 0.95f));
            var layout = card.AddComponent<VerticalLayoutGroup>();
            ConfigureCardLayout(layout);
            SetFlexibleHeight(card, 0f, 0f);

            Text title = MakeText(card.transform, "Title", 18, FontStyle.Bold, TextAnchor.MiddleLeft);
            title.text = "状态";
            SetPreferredHeight(title.gameObject, 24f);

            _statusText = MakeText(card.transform, "StatusText", 16, FontStyle.Normal, TextAnchor.UpperLeft);
            _statusText.text = "等待视觉服务…";
            _statusText.color = normalColor;
            SetPreferredHeight(_statusText.gameObject, 40f);

            GameObject logGo = NewUiObject("Log", card.transform);
            var image = logGo.AddComponent<Image>();
            image.color = new Color(0.05f, 0.06f, 0.08f, 1f);
            SetPreferredHeight(logGo, 90f);

            GameObject viewport = NewUiObject("Viewport", logGo.transform);
            Stretch(viewport.GetComponent<RectTransform>(), 0f);

            GameObject content = NewUiObject("Content", viewport.transform);
            RectTransform contentRect = content.GetComponent<RectTransform>();
            contentRect.anchorMin = new Vector2(0f, 1f);
            contentRect.anchorMax = new Vector2(1f, 1f);
            contentRect.pivot = new Vector2(0.5f, 1f);
            contentRect.sizeDelta = new Vector2(0f, 0f);

            var fitter = content.AddComponent<ContentSizeFitter>();
            fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            _logText = MakeText(content.transform, "LogText", 14, FontStyle.Normal, TextAnchor.UpperLeft);
            _logText.text = string.Empty;
            _logText.color = secondaryColor;
            RectTransform logRect = _logText.rectTransform;
            logRect.anchorMin = new Vector2(0f, 1f);
            logRect.anchorMax = new Vector2(1f, 1f);
            logRect.pivot = new Vector2(0.5f, 1f);
            logRect.anchoredPosition = Vector2.zero;
            logRect.sizeDelta = new Vector2(0f, 0f);

            var scroll = logGo.AddComponent<ScrollRect>();
            scroll.content = contentRect;
            scroll.viewport = viewport.GetComponent<RectTransform>();
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
        }

        private static void ConfigureCardLayout(VerticalLayoutGroup layout)
        {
            layout.spacing = 6f;
            layout.padding = new RectOffset(8, 8, 8, 8);
            layout.childAlignment = TextAnchor.UpperLeft;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;
        }

        // ------------------------------------------------------------------
        // Small uGUI factories
        // ------------------------------------------------------------------

        /// <summary>
        /// Finds or creates the single EventSystem the canvas needs.
        ///
        /// Without one, no button click is ever delivered. Existing instances are
        /// reused rather than duplicated, and a duplicate is reported because two
        /// EventSystems in one scene is a real (and hard to spot) configuration
        /// error.
        /// </summary>
        private static void EnsureEventSystem()
        {
            int count = 0;
            var systems = Object.FindObjectsOfType<EventSystem>(true);
            EventSystem first = null;
            for (int i = 0; i < systems.Length; i++)
            {
                if (systems[i] == null)
                {
                    continue;
                }

                count++;
                if (first == null)
                {
                    first = systems[i];
                }
            }

            if (count == 0)
            {
                var go = new GameObject("EventSystem");
                go.AddComponent<EventSystem>();
                go.AddComponent<StandaloneInputModule>();
                return;
            }

            if (count > 1)
            {
                Debug.LogWarning("[vision] multiple EventSystems found (" + count + "); keep exactly one.");
            }

            if (first.GetComponent<BaseInputModule>() == null)
            {
                first.gameObject.AddComponent<StandaloneInputModule>();
            }
        }

        private static GameObject NewUiObject(string name, Transform parent)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return go;
        }

        private static GameObject NewCard(GameObject parent, string name, Color color)
        {
            GameObject go = NewUiObject(name, parent.transform);
            go.AddComponent<Image>().color = color;
            return go;
        }

        private static void Stretch(RectTransform rect, float inset)
        {
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(inset, inset);
            rect.offsetMax = new Vector2(-inset, -inset);
            rect.pivot = new Vector2(0.5f, 0.5f);
        }

        private static void SetFixedWidth(GameObject go, float width)
        {
            var element = go.GetComponent<LayoutElement>();
            if (element == null)
            {
                element = go.AddComponent<LayoutElement>();
            }

            if (width < 0f)
            {
                element.flexibleWidth = 1f;
                element.minWidth = 320f;
            }
            else
            {
                element.preferredWidth = width;
                element.minWidth = width;
                element.flexibleWidth = 0f;
            }
        }

        private static void SetPreferredHeight(GameObject go, float height)
        {
            var element = go.GetComponent<LayoutElement>();
            if (element == null)
            {
                element = go.AddComponent<LayoutElement>();
            }

            element.minHeight = height;
            element.preferredHeight = height;
            element.flexibleHeight = 0f;
        }

        private static void SetFlexibleHeight(GameObject go, float flexible, float preferred)
        {
            var element = go.GetComponent<LayoutElement>();
            if (element == null)
            {
                element = go.AddComponent<LayoutElement>();
            }

            element.flexibleHeight = flexible;
            if (preferred > 0f)
            {
                element.preferredHeight = preferred;
                element.minHeight = preferred;
            }
        }

        private static GameObject MakeRow(GameObject parent, string name, float height)
        {
            GameObject row = NewUiObject(name, parent.transform);
            var layout = row.AddComponent<HorizontalLayoutGroup>();
            layout.spacing = 6f;
            layout.childAlignment = TextAnchor.MiddleLeft;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = true;
            SetPreferredHeight(row, height);
            return row;
        }

        private static Text MakeText(Transform parent, string name, int size, FontStyle style, TextAnchor anchor)
        {
            GameObject go = NewUiObject(name, parent);
            var text = go.AddComponent<Text>();
            text.font = UiFont();
            text.fontSize = size;
            text.fontStyle = style;
            text.alignment = anchor;
            text.color = Color.white;
            text.horizontalOverflow = HorizontalWrapMode.Wrap;
            text.verticalOverflow = VerticalWrapMode.Truncate;
            text.raycastTarget = false;
            return text;
        }

        private static Button MakeButton(Transform parent, string name, string label, int size, Color background)
        {
            GameObject go = NewUiObject(name, parent);
            var image = go.AddComponent<Image>();
            image.color = background;

            var button = go.AddComponent<Button>();
            button.targetGraphic = image;

            Text text = MakeText(go.transform, "Label", size, FontStyle.Normal, TextAnchor.MiddleCenter);
            Stretch(text.rectTransform, 2f);
            text.text = label;
            return button;
        }

        private static InputField MakeInputField(Transform parent, string name, string placeholder, float height)
        {
            GameObject go = NewUiObject(name, parent);

            var image = go.AddComponent<Image>();
            image.color = new Color(0.07f, 0.08f, 0.11f, 1f);

            GameObject textGo = NewUiObject("Text", go.transform);
            var text = textGo.AddComponent<Text>();
            text.font = UiFont();
            text.fontSize = 15;
            text.alignment = TextAnchor.MiddleLeft;
            text.color = Color.white;
            text.supportRichText = false;
            text.horizontalOverflow = HorizontalWrapMode.Overflow;
            text.verticalOverflow = VerticalWrapMode.Truncate;
            Stretch(text.rectTransform, 6f);
            text.rectTransform.offsetMax = new Vector2(-6f, -2f);

            GameObject placeholderGo = NewUiObject("Placeholder", go.transform);
            var hint = placeholderGo.AddComponent<Text>();
            hint.font = UiFont();
            hint.fontSize = 15;
            hint.alignment = TextAnchor.MiddleLeft;
            hint.color = new Color(0.55f, 0.58f, 0.64f, 1f);
            hint.text = placeholder;
            Stretch(hint.rectTransform, 6f);

            var field = go.AddComponent<InputField>();
            field.textComponent = text;
            field.placeholder = hint;
            field.targetGraphic = image;
            field.lineType = InputField.LineType.SingleLine;
            SetPreferredHeight(go, height);
            return field;
        }

        /// <summary>
        /// Builds a complete Dropdown, including the template it clones when the
        /// list opens. uGUI needs the whole template hierarchy up front, which is
        /// why this is more code than the other controls.
        /// </summary>
        private static Dropdown MakeDropdown(Transform parent, string name, float height)
        {
            GameObject go = NewUiObject(name, parent);
            var image = go.AddComponent<Image>();
            image.color = new Color(0.07f, 0.08f, 0.11f, 1f);

            GameObject labelGo = NewUiObject("Label", go.transform);
            var label = labelGo.AddComponent<Text>();
            label.font = UiFont();
            label.fontSize = 15;
            label.alignment = TextAnchor.MiddleLeft;
            label.color = Color.white;
            Stretch(label.rectTransform, 6f);
            label.rectTransform.offsetMax = new Vector2(-22f, -2f);

            GameObject arrowGo = NewUiObject("Arrow", go.transform);
            var arrow = arrowGo.AddComponent<Text>();
            arrow.font = UiFont();
            arrow.fontSize = 14;
            arrow.alignment = TextAnchor.MiddleCenter;
            arrow.color = Color.white;
            arrow.text = "v";
            RectTransform arrowRect = arrow.rectTransform;
            arrowRect.anchorMin = new Vector2(1f, 0f);
            arrowRect.anchorMax = new Vector2(1f, 1f);
            arrowRect.pivot = new Vector2(1f, 0.5f);
            arrowRect.sizeDelta = new Vector2(18f, 0f);
            arrowRect.anchoredPosition = new Vector2(-4f, 0f);

            GameObject templateGo = NewUiObject("Template", go.transform);
            var templateImage = templateGo.AddComponent<Image>();
            templateImage.color = new Color(0.09f, 0.1f, 0.14f, 0.98f);
            RectTransform templateRect = templateGo.GetComponent<RectTransform>();
            templateRect.anchorMin = new Vector2(0f, 0f);
            templateRect.anchorMax = new Vector2(1f, 0f);
            templateRect.pivot = new Vector2(0.5f, 1f);
            templateRect.anchoredPosition = new Vector2(0f, 2f);
            templateRect.sizeDelta = new Vector2(0f, 150f);

            var scroll = templateGo.AddComponent<ScrollRect>();
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;

            GameObject viewport = NewUiObject("Viewport", templateGo.transform);
            viewport.AddComponent<Image>().color = new Color(1f, 1f, 1f, 0.01f);
            viewport.AddComponent<Mask>().showMaskGraphic = false;
            Stretch(viewport.GetComponent<RectTransform>(), 0f);

            GameObject content = NewUiObject("Content", viewport.transform);
            RectTransform contentRect = content.GetComponent<RectTransform>();
            contentRect.anchorMin = new Vector2(0f, 1f);
            contentRect.anchorMax = new Vector2(1f, 1f);
            contentRect.pivot = new Vector2(0.5f, 1f);
            contentRect.sizeDelta = new Vector2(0f, 30f);

            GameObject item = NewUiObject("Item", content.transform);
            RectTransform itemRect = item.GetComponent<RectTransform>();
            itemRect.anchorMin = new Vector2(0f, 0.5f);
            itemRect.anchorMax = new Vector2(1f, 0.5f);
            itemRect.pivot = new Vector2(0.5f, 0.5f);
            itemRect.sizeDelta = new Vector2(0f, 30f);
            var itemImage = item.AddComponent<Image>();
            itemImage.color = new Color(0.16f, 0.2f, 0.28f, 1f);
            var toggle = item.AddComponent<Toggle>();
            toggle.targetGraphic = itemImage;

            GameObject itemBackground = NewUiObject("Item Background", item.transform);
            itemBackground.AddComponent<Image>().color = new Color(0.12f, 0.15f, 0.2f, 1f);
            Stretch(itemBackground.GetComponent<RectTransform>(), 0f);

            GameObject itemCheckmark = NewUiObject("Item Checkmark", item.transform);
            var checkmark = itemCheckmark.AddComponent<Text>();
            checkmark.font = UiFont();
            checkmark.fontSize = 14;
            checkmark.alignment = TextAnchor.MiddleCenter;
            checkmark.color = new Color(0.5f, 0.95f, 0.6f, 1f);
            checkmark.text = ">";
            RectTransform checkRect = checkmark.rectTransform;
            checkRect.anchorMin = new Vector2(0f, 0f);
            checkRect.anchorMax = new Vector2(0f, 1f);
            checkRect.pivot = new Vector2(0f, 0.5f);
            checkRect.sizeDelta = new Vector2(16f, 0f);
            checkRect.anchoredPosition = new Vector2(2f, 0f);

            GameObject itemLabel = NewUiObject("Item Label", item.transform);
            var itemText = itemLabel.AddComponent<Text>();
            itemText.font = UiFont();
            itemText.fontSize = 15;
            itemText.alignment = TextAnchor.MiddleLeft;
            itemText.color = Color.white;
            Stretch(itemText.rectTransform, 4f);
            itemText.rectTransform.offsetMin = new Vector2(18f, 2f);

            toggle.graphic = checkmark;

            GameObject scrollbarGo = NewUiObject("Scrollbar", templateGo.transform);
            var scrollbarImage = scrollbarGo.AddComponent<Image>();
            scrollbarImage.color = new Color(0.2f, 0.22f, 0.28f, 1f);
            RectTransform scrollbarRect = scrollbarGo.GetComponent<RectTransform>();
            scrollbarRect.anchorMin = new Vector2(1f, 0f);
            scrollbarRect.anchorMax = new Vector2(1f, 1f);
            scrollbarRect.pivot = new Vector2(1f, 0.5f);
            scrollbarRect.sizeDelta = new Vector2(8f, 0f);
            var scrollbar = scrollbarGo.AddComponent<Scrollbar>();
            scrollbar.direction = Scrollbar.Direction.BottomToTop;

            GameObject slidingArea = NewUiObject("Sliding Area", scrollbarGo.transform);
            Stretch(slidingArea.GetComponent<RectTransform>(), 0f);

            GameObject handle = NewUiObject("Handle", slidingArea.transform);
            var handleImage = handle.AddComponent<Image>();
            handleImage.color = new Color(0.45f, 0.5f, 0.62f, 1f);
            Stretch(handle.GetComponent<RectTransform>(), 0f);
            scrollbar.handleRect = handle.GetComponent<RectTransform>();
            scrollbar.targetGraphic = handleImage;

            scroll.content = contentRect;
            scroll.viewport = viewport.GetComponent<RectTransform>();
            scroll.verticalScrollbar = scrollbar;
            scroll.verticalScrollbarVisibility = ScrollRect.ScrollbarVisibility.AutoHideAndExpandViewport;
            scroll.verticalScrollbarSpacing = -2f;

            var dropdown = go.AddComponent<Dropdown>();
            dropdown.targetGraphic = image;
            dropdown.template = templateRect;
            dropdown.captionText = label;
            dropdown.itemText = itemText;
            SetPreferredHeight(go, height);

            templateGo.SetActive(false);
            return dropdown;
        }

        /// <summary>
        /// Font used by every generated label.
        ///
        /// The panel shows Chinese text, so a font that can actually render it is
        /// preferred; Unity's built-in legacy font is the fallback and the panel
        /// still works (with missing glyphs) if no system font resolves.
        /// </summary>
        private static Font UiFont()
        {
            if (_font != null)
            {
                return _font;
            }

            string[] candidates =
            {
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "SimHei",
                "Noto Sans CJK SC",
                "Arial Unicode MS",
            };

            for (int i = 0; i < candidates.Length; i++)
            {
                try
                {
                    Font font = Font.CreateDynamicFontFromOSFont(candidates[i], 16);
                    if (font != null)
                    {
                        _font = font;
                        return _font;
                    }
                }
                catch (System.Exception)
                {
                    // A missing system font is not fatal; try the next candidate.
                }
            }

            _font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            if (_font == null)
            {
                _font = Resources.GetBuiltinResource<Font>("Arial.ttf");
            }

            return _font;
        }
    }
}
