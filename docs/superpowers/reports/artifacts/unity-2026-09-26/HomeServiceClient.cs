using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using UnityEngine;
using UnityEngine.Networking;

namespace SmartHome
{
    public sealed class HomeServiceClient : MonoBehaviour
    {
        [Tooltip("Loopback home-service URL.")]
        public string baseUrl = "http://127.0.0.1:8765";

        [Min(0.25f)] public float pollIntervalSeconds = 1f;
        [Min(0.5f)] public float reconnectDelaySeconds = 3f;
        public bool writeDiagnostics = true;

        public event Action<HomeSnapshot> SnapshotReceived;
        public event Action<string> StatusChanged;
        public event Action<string> ErrorOccurred;

        public bool Connected { get; private set; }
        public string Backend { get; private set; } = "unknown";
        public HomeSnapshot LatestSnapshot { get; private set; }
        public long LastSseLagMs { get; private set; } = -1;
        public double LastApplyMs { get; private set; } = -1;
        public string[] LastTriggerIds { get; private set; } = Array.Empty<string>();
        public string DiagnosticsPath => Path.Combine(Application.persistentDataPath, "smart-home-diagnostics", "unity.jsonl");

        private Coroutine _connectionRoutine;
        private UnityWebRequest _activeRequest;

        private void OnEnable()
        {
            StartPolling();
        }

        private void OnDisable()
        {
            StopPolling();
        }

        public void StartPolling()
        {
            if (_connectionRoutine == null && isActiveAndEnabled)
            {
                _connectionRoutine = StartCoroutine(ConnectionLoop());
            }
        }

        public void StopPolling()
        {
            if (_activeRequest != null)
            {
                _activeRequest.Abort();
                _activeRequest.Dispose();
                _activeRequest = null;
            }

            if (_connectionRoutine != null)
            {
                StopCoroutine(_connectionRoutine);
                _connectionRoutine = null;
            }

            Connected = false;
        }

        private IEnumerator ConnectionLoop()
        {
            while (true)
            {
                bool useLegacyPolling = false;
                yield return ConnectEventStream(value => useLegacyPolling = value);
                if (useLegacyPolling)
                {
                    yield return PollLegacy();
                    yield break;
                }

                yield return new WaitForSecondsRealtime(reconnectDelaySeconds);
            }
        }

        private IEnumerator ConnectEventStream(Action<bool> completed)
        {
            string eventsUrl = baseUrl.TrimEnd('/') + "/events";
            var handler = new HomeServiceDownloadHandler();
            using (var request = new UnityWebRequest(eventsUrl, UnityWebRequest.kHttpVerbGET))
            {
                request.downloadHandler = handler;
                request.SetRequestHeader("Accept", "text/event-stream");
                _activeRequest = request;
                UnityWebRequestAsyncOperation operation = request.SendWebRequest();
                while (!operation.isDone)
                {
                    DrainStreamEvents(handler.Decoder);
                    yield return null;
                }

                DrainStreamEvents(handler.Decoder);
                if (_activeRequest == request)
                {
                    _activeRequest = null;
                }

                if (request.responseCode == 404)
                {
                    completed(true);
                    yield break;
                }

                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetDisconnected("SSE 连接中断: " + request.error);
                }
                else
                {
                    SetDisconnected("SSE 连接已关闭");
                }

                completed(false);
            }
        }

        private void DrainStreamEvents(HomeServiceEventDecoder decoder)
        {
            foreach (HomeServiceEvent item in decoder.Drain())
            {
                if (item.Event == "snapshot")
                {
                    ParseAndPublishStream(item.Data);
                }
                else if (item.Event == "status")
                {
                    HandleStreamStatus(item.Data);
                }
            }
        }

        private bool ParseAndPublishStream(string json)
        {
            try
            {
                StreamMetadata metadata = JsonUtility.FromJson<StreamMetadata>(json);
                HomeSnapshot snapshot = HomeSnapshot.FromStreamJson(json);
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                LastSseLagMs = metadata != null && metadata.generated_at_ms > 0
                    ? Math.Max(0, nowMs - metadata.generated_at_ms) : -1;
                LastTriggerIds = metadata != null && metadata.trigger_ids != null
                    ? metadata.trigger_ids : Array.Empty<string>();
                var applyTimer = System.Diagnostics.Stopwatch.StartNew();
                Backend = metadata != null && metadata.backend == "memory" ? "Memory" : "Home Assistant";
                LatestSnapshot = snapshot;
                SetConnected();
                SnapshotReceived?.Invoke(snapshot);
                applyTimer.Stop();
                LastApplyMs = applyTimer.Elapsed.TotalMilliseconds;
                if (LastSseLagMs >= 0)
                {
                    WriteMetric("sse_arrival", LastSseLagMs, LastTriggerIds);
                }
                WriteMetric("snapshot_apply", LastApplyMs, LastTriggerIds);
                return true;
            }
            catch (Exception ex)
            {
                SetDisconnected("状态解析失败: " + ex.Message);
                return false;
            }
        }

        private void HandleStreamStatus(string json)
        {
            StreamStatusPayload payload;
            try
            {
                payload = JsonUtility.FromJson<StreamStatusPayload>(json);
            }
            catch (ArgumentException)
            {
                return;
            }

            if (payload == null)
            {
                return;
            }

            if (payload.state == "upstream_disconnected" || payload.state == "upstream_error")
            {
                Connected = false;
                StatusChanged?.Invoke("HA 已断开");
            }
            else if (payload.state == "upstream_connected")
            {
                StatusChanged?.Invoke("Home Assistant 已连接");
            }
        }

        private IEnumerator PollLegacy()
        {
            while (true)
            {
                bool succeeded = false;
                yield return FetchSnapshot(value => succeeded = value);
                yield return new WaitForSecondsRealtime(succeeded ? pollIntervalSeconds : reconnectDelaySeconds);
            }
        }

        private IEnumerator FetchSnapshot(Action<bool> completed)
        {
            string snapshotUrl = baseUrl.TrimEnd('/') + "/snapshot";
            using (UnityWebRequest request = UnityWebRequest.Get(snapshotUrl))
            {
                request.timeout = 5;
                yield return request.SendWebRequest();

                if (request.result == UnityWebRequest.Result.Success)
                {
                    completed(ParseAndPublish(request.downloadHandler.text, false));
                    yield break;
                }

                if (request.responseCode != 404)
                {
                    SetDisconnected("home-service 不可达: " + request.error);
                    completed(false);
                    yield break;
                }
            }

            string haUrl = baseUrl.TrimEnd('/') + "/tool/room_status";
            using (UnityWebRequest request = UnityWebRequest.Get(haUrl))
            {
                request.timeout = 5;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetDisconnected("HA 状态读取失败: " + request.error);
                    completed(false);
                    yield break;
                }

                completed(ParseAndPublish(request.downloadHandler.text, true));
            }
        }

        private bool ParseAndPublish(string json, bool haBackend)
        {
            try
            {
                HomeSnapshot snapshot = haBackend
                    ? HomeSnapshot.FromHaRoomStatusJson(json)
                    : HomeSnapshot.FromMemoryJson(json);
                Backend = haBackend ? "Home Assistant" : "Memory";
                LatestSnapshot = snapshot;
                SetConnected();
                SnapshotReceived?.Invoke(snapshot);
                return true;
            }
            catch (Exception ex)
            {
                SetDisconnected("状态解析失败: " + ex.Message);
                return false;
            }
        }

        private void SetConnected()
        {
            bool changed = !Connected;
            Connected = true;
            if (changed)
            {
                StatusChanged?.Invoke("已连接 · " + Backend);
            }
        }

        private void SetDisconnected(string message)
        {
            Connected = false;
            StatusChanged?.Invoke("未连接");
            ErrorOccurred?.Invoke(message);
        }

        private void WriteMetric(string stage, double durationMs, string[] triggerIds)
        {
            if (!writeDiagnostics) return;
            try
            {
                string path = DiagnosticsPath;
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                if (File.Exists(path) && new FileInfo(path).Length >= 1048576)
                {
                    string backup = path + ".1";
                    if (File.Exists(backup)) File.Delete(backup);
                    File.Move(path, backup);
                }
                var safeIds = new List<string>();
                if (triggerIds != null)
                {
                    foreach (string id in triggerIds)
                    {
                        if (id != null && Regex.IsMatch(id, "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"))
                            safeIds.Add(HashId(id));
                    }
                }
                AppendMetric(path, stage, durationMs, safeIds.ToArray());
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (ArgumentException) { }
            catch (NotSupportedException) { }
            catch (System.Security.SecurityException) { }
        }

        private static void AppendMetric(string path, string stage, double durationMs, string[] triggerIds)
        {
            var record = new DiagnosticRecord {
                at_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                component = "unity", stage = stage, outcome = "ok", run_kind = "continuous",
                duration_ms = (float)Math.Max(0, durationMs),
                trigger_ids = triggerIds,
            };
            File.AppendAllText(path, JsonUtility.ToJson(record) + "\n", Encoding.UTF8);
        }

        private static string HashId(string value)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(value));
                var builder = new StringBuilder(24);
                for (int index = 0; index < 12; index++) builder.Append(digest[index].ToString("x2"));
                return builder.ToString();
            }
        }

        [Serializable]
        private sealed class DiagnosticRecord
        {
            public long at_ms;
            public string component;
            public string stage;
            public string outcome;
            public string run_kind;
            public float duration_ms;
            public string[] trigger_ids;
        }

        [Serializable]
        private sealed class StreamMetadata
        {
            public string backend;
            public long generated_at_ms;
            public string[] trigger_ids;
        }

        [Serializable]
        private sealed class StreamStatusPayload
        {
            public string state;
        }
    }
}
