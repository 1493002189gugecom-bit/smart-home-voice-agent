using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace SmartHome
{
    /// <summary>
    /// Talks to the local home-service over loopback HTTP.
    ///
    /// Only the authoritative state service mutates state; this client reads
    /// snapshots and increments, and reports playback receipts back. It never
    /// invents device state.
    /// </summary>
    public sealed class HomeServiceClient : MonoBehaviour
    {
        [Tooltip("Loopback address of the home-service. LAN addresses are not used.")]
        public string baseUrl = "http://127.0.0.1:8765";

        [Tooltip("Seconds between reconnect attempts after a failure.")]
        public float reconnectDelaySeconds = 3f;

        public event Action<HomeSnapshot> SnapshotReceived;
        public event Action<string> StatusChanged;
        public event Action<string> ErrorOccurred;

        /// <summary>Last canonical snapshot version this client has applied.</summary>
        public int AppliedVersion { get; private set; } = -1;

        public bool Connected { get; private set; }

        private Coroutine _pollRoutine;

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
        }

        private IEnumerator PollLoop()
        {
            // Person positions are camera observations with their own expiry and
            // do not mutate the legacy HomeState increment counter. Poll the
            // canonical full snapshot so device and visual state always advance
            // together on both memory and Home Assistant backends.
            while (true)
            {
                yield return SyncOnce();
                yield return new WaitForSecondsRealtime(1f);
            }
        }

        private IEnumerator SyncOnce()
        {
            const string path = "/snapshot";
            using (UnityWebRequest request = UnityWebRequest.Get(baseUrl + path))
            {
                request.timeout = 5;
                yield return request.SendWebRequest();

#if UNITY_2020_1_OR_NEWER
                bool failed = request.result != UnityWebRequest.Result.Success;
#else
                bool failed = request.isNetworkError || request.isHttpError;
#endif
                if (failed)
                {
                    SetDisconnected(request.error);
                    yield return new WaitForSecondsRealtime(reconnectDelaySeconds);
                    yield break;
                }

                string json = request.downloadHandler.text;
                HomeSnapshot snapshot;
                try
                {
                    snapshot = StateParser.ParseSync(json);
                }
                catch (Exception ex)
                {
                    ErrorOccurred?.Invoke("state parse failed: " + ex.Message);
                    yield break;
                }

                AppliedVersion = snapshot.version;
                SetConnected();
                SnapshotReceived?.Invoke(snapshot);
            }
        }

        /// <summary>Report the playback receipt for a broadcast task.</summary>
        public IEnumerator SendBroadcastReceipt(string taskId, string receiptId, bool success, string error)
        {
            string body = "{\"task_id\":\"" + taskId + "\",\"receipt_id\":\"" + receiptId +
                          "\",\"success\":" + (success ? "true" : "false") +
                          ",\"error\":" + (string.IsNullOrEmpty(error) ? "null" : "\"" + error + "\"") + "}";
            yield return PostJson("/broadcast/receipt", body, null);
        }

        public IEnumerator RequestBroadcastStart(string taskId, Action<string> onReceiptId)
        {
            string body = "{\"task_id\":\"" + taskId + "\"}";
            yield return PostJson("/broadcast/start", body, onReceiptId);
        }

        private IEnumerator PostJson(string path, string body, Action<string> onResponse)
        {
            byte[] payload = Encoding.UTF8.GetBytes(body);
            using (UnityWebRequest request = new UnityWebRequest(baseUrl + path, "POST"))
            {
                request.uploadHandler = new UploadHandlerRaw(payload);
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                request.timeout = 5;
                yield return request.SendWebRequest();

#if UNITY_2020_1_OR_NEWER
                bool failed = request.result != UnityWebRequest.Result.Success;
#else
                bool failed = request.isNetworkError || request.isHttpError;
#endif
                if (failed)
                {
                    ErrorOccurred?.Invoke(path + " failed: " + request.error);
                    yield break;
                }

                if (onResponse != null)
                {
                    onResponse(StateParser.ExtractReceiptId(request.downloadHandler.text));
                }
            }
        }

        private void SetConnected()
        {
            if (!Connected)
            {
                Connected = true;
                StatusChanged?.Invoke("connected");
            }
        }

        private void SetDisconnected(string reason)
        {
            if (Connected)
            {
                StatusChanged?.Invoke("disconnected: " + reason);
            }

            Connected = false;
        }
    }
}
