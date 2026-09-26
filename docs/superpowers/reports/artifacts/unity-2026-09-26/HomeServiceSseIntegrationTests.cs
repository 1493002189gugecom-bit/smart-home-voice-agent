using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace SmartHome.Tests
{
    public sealed class HomeServiceSseIntegrationTests
    {
        private const string SnapshotJson =
            "{\"version\":7,\"backend\":\"ha\",\"generated_at_ms\":1,\"trigger_ids\":[\"vision-abc:123\",\"op-abc\"]," +
            "\"rooms\":[{\"id\":\"living_room\",\"name\":\"客厅\",\"devices\":null}]," +
            "\"devices\":[{\"id\":\"living_room_light\",\"room_id\":\"living_room\",\"name\":\"客厅灯\",\"type\":\"light\",\"online\":true,\"state\":{\"on\":true,\"brightness\":50},\"version\":7}]," +
            "\"persons\":[],\"broadcasts\":[],\"broadcast_queue\":[]}";

        [UnityTest]
        public IEnumerator HealthyStreamIsPreferredOverLegacyPolling()
        {
            using (var server = new FakeHomeService(eventsAvailable: true))
            {
                var root = new GameObject("SseClientTest");
                HomeServiceClient client = root.AddComponent<HomeServiceClient>();
                client.StopPolling();
                client.writeDiagnostics = false;
                client.baseUrl = server.BaseUrl;
                client.reconnectDelaySeconds = 10f;
                HomeSnapshot received = null;
                client.SnapshotReceived += value => received = value;
                client.StartPolling();

                yield return WaitFor(() => received != null, 5f);

                Assert.That(received, Is.Not.Null, "the streamed snapshot was not published");
                Assert.That(client.Backend, Is.EqualTo("Home Assistant"));
                CollectionAssert.AreEqual(new[] { "vision-abc:123", "op-abc" }, client.LastTriggerIds);
                Assert.That(client.LastSseLagMs, Is.GreaterThan(0));
                Assert.That(client.LastApplyMs, Is.GreaterThanOrEqualTo(0));
                CollectionAssert.AreEqual(new[] { "/events" }, server.Paths);
                UnityEngine.Object.Destroy(root);
            }
        }

        [UnityTest]
        public IEnumerator Events404FallsBackToLegacySnapshot()
        {
            using (var server = new FakeHomeService(eventsAvailable: false))
            {
                var root = new GameObject("LegacyFallbackTest");
                HomeServiceClient client = root.AddComponent<HomeServiceClient>();
                client.StopPolling();
                client.writeDiagnostics = false;
                client.baseUrl = server.BaseUrl;
                HomeSnapshot received = null;
                client.SnapshotReceived += value => received = value;
                client.StartPolling();

                yield return WaitFor(() => received != null, 5f);

                Assert.That(received, Is.Not.Null, "legacy /snapshot fallback did not publish");
                Assert.That(client.Backend, Is.EqualTo("Memory"));
                CollectionAssert.AreEqual(new[] { "/events", "/snapshot" }, server.Paths);
                UnityEngine.Object.Destroy(root);
            }
        }

        [UnityTest]
        public IEnumerator DroppedStreamReconnectsWithoutFallingBackToPolling()
        {
            using (var server = new FakeHomeService(eventsAvailable: true, failuresBeforeSse: 1))
            {
                var root = new GameObject("SseReconnectTest");
                HomeServiceClient client = root.AddComponent<HomeServiceClient>();
                client.StopPolling();
                client.writeDiagnostics = false;
                client.baseUrl = server.BaseUrl;
                client.reconnectDelaySeconds = 0.05f;
                HomeSnapshot received = null;
                client.SnapshotReceived += value => received = value;
                client.StartPolling();

                yield return WaitFor(() => received != null, 5f);

                Assert.That(received, Is.Not.Null, "the client did not recover after the first stream failed");
                CollectionAssert.AreEqual(new[] { "/events", "/events" }, server.Paths);
                UnityEngine.Object.Destroy(root);
            }
        }

        private static IEnumerator WaitFor(Func<bool> condition, float timeoutSeconds)
        {
            float deadline = Time.realtimeSinceStartup + timeoutSeconds;
            while (!condition() && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }
        }

        private sealed class FakeHomeService : IDisposable
        {
            private readonly TcpListener _listener;
            private readonly Thread _thread;
            private readonly bool _eventsAvailable;
            private readonly int _failuresBeforeSse;
            private int _eventAttempts;
            private volatile bool _stopping;
            private readonly List<string> _paths = new List<string>();

            public FakeHomeService(bool eventsAvailable, int failuresBeforeSse = 0)
            {
                _eventsAvailable = eventsAvailable;
                _failuresBeforeSse = failuresBeforeSse;
                _listener = new TcpListener(IPAddress.Loopback, 0);
                _listener.Start();
                int port = ((IPEndPoint)_listener.LocalEndpoint).Port;
                BaseUrl = "http://127.0.0.1:" + port;
                _thread = new Thread(Run) { IsBackground = true, Name = "fake-home-service" };
                _thread.Start();
            }

            public string BaseUrl { get; }

            public string[] Paths
            {
                get
                {
                    lock (_paths)
                    {
                        return _paths.ToArray();
                    }
                }
            }

            public void Dispose()
            {
                _stopping = true;
                _listener.Stop();
                _thread.Join(1000);
            }

            private void Run()
            {
                while (!_stopping)
                {
                    try
                    {
                        using (TcpClient client = _listener.AcceptTcpClient())
                        using (NetworkStream stream = client.GetStream())
                        {
                            string requestLine = ReadRequestLine(stream);
                            string[] parts = requestLine.Split(' ');
                            string path = parts.Length > 1 ? parts[1] : string.Empty;
                            lock (_paths)
                            {
                                _paths.Add(path);
                            }

                            if (path == "/events" && _eventsAvailable && ++_eventAttempts > _failuresBeforeSse)
                            {
                                string body = "id: 1\nevent: snapshot\ndata: " + SnapshotJson + "\n\n";
                                WriteResponse(stream, 200, "text/event-stream; charset=utf-8", body);
                            }
                            else if (path == "/events" && _eventsAvailable)
                            {
                                WriteResponse(stream, 503, "application/json", "{\"error\":\"unavailable\"}");
                            }
                            else if (path == "/snapshot" && !_eventsAvailable)
                            {
                                string memory = SnapshotJson.Replace("\"backend\":\"ha\"", "\"backend\":\"memory\"");
                                WriteResponse(stream, 200, "application/json", memory);
                            }
                            else
                            {
                                WriteResponse(stream, 404, "application/json", "{\"error\":\"not_found\"}");
                            }
                        }
                    }
                    catch (SocketException) when (_stopping)
                    {
                        return;
                    }
                    catch (ObjectDisposedException) when (_stopping)
                    {
                        return;
                    }
                }
            }

            private static string ReadRequestLine(NetworkStream stream)
            {
                var bytes = new List<byte>();
                int current;
                while ((current = stream.ReadByte()) >= 0)
                {
                    bytes.Add((byte)current);
                    int count = bytes.Count;
                    if (count >= 4 && bytes[count - 4] == '\r' && bytes[count - 3] == '\n' &&
                        bytes[count - 2] == '\r' && bytes[count - 1] == '\n')
                    {
                        break;
                    }
                }

                string request = Encoding.ASCII.GetString(bytes.ToArray());
                int end = request.IndexOf("\r\n", StringComparison.Ordinal);
                return end >= 0 ? request.Substring(0, end) : request;
            }

            private static void WriteResponse(NetworkStream stream, int status, string contentType, string body)
            {
                byte[] payload = Encoding.UTF8.GetBytes(body);
                string reason = status == 200 ? "OK" : status == 404 ? "Not Found" : "Service Unavailable";
                string headers = "HTTP/1.1 " + status + " " + reason + "\r\n" +
                                 "Content-Type: " + contentType + "\r\n" +
                                 "Content-Length: " + payload.Length + "\r\n" +
                                 "Connection: close\r\n\r\n";
                byte[] headerBytes = Encoding.ASCII.GetBytes(headers);
                stream.Write(headerBytes, 0, headerBytes.Length);
                stream.Write(payload, 0, payload.Length);
                stream.Flush();
            }
        }
    }
}
