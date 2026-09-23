using System.Collections.Generic;
using UnityEngine;

namespace SmartHome
{
    /// <summary>
    /// Applies authoritative state to the scene.
    ///
    /// Person positions now originate from camera observations: the home service
    /// only publishes a room once the vision pipeline has confirmed an identity,
    /// so this view follows the snapshot and never guesses a room. A person whose
    /// location is unknown is moved to an explicit staging area instead of being
    /// left where they were last seen, because a marker that stays behind reads as
    /// a live position when it is not. Broadcast playback is driven by the queue
    /// head so only one room can ever be "playing".
    /// </summary>
    public sealed class SceneStateApplier : MonoBehaviour
    {
        [Tooltip("Client that feeds state into this applier.")]
        public HomeServiceClient client;

        [Tooltip("Highlight colour for the room currently being announced.")]
        public Color broadcastingColor = new Color(1f, 0.85f, 0.3f, 1f);

        public Color idleColor = new Color(0.35f, 0.55f, 0.75f, 1f);

        [Tooltip("Height above the floor where a person marker floats.")]
        public float personHeight = 0.4f;

        [Tooltip("Height of the unknown-location staging area above the room floor.")]
        public float unknownStagingHeight = 2.6f;

        [Tooltip("Colour of a person whose room is known.")]
        public Color locatedPersonColor = Color.green;

        [Tooltip("Colour of a person whose room is not known.")]
        public Color unknownPersonColor = Color.red;

        private readonly Dictionary<string, Transform> _deviceViews = new Dictionary<string, Transform>();
        private readonly Dictionary<string, Transform> _personViews = new Dictionary<string, Transform>();
        private readonly Dictionary<string, Renderer> _roomRenderers = new Dictionary<string, Renderer>();
        private readonly Dictionary<string, TextMesh> _deviceLabels = new Dictionary<string, TextMesh>();
        private readonly Dictionary<string, TextMesh> _personLabels = new Dictionary<string, TextMesh>();

        private HomeSnapshot _snapshot;

        private void OnEnable()
        {
            if (client != null)
            {
                client.SnapshotReceived += Apply;
                client.StatusChanged += OnStatus;
                client.ErrorOccurred += OnError;
            }
        }

        private void OnDisable()
        {
            if (client != null)
            {
                client.SnapshotReceived -= Apply;
                client.StatusChanged -= OnStatus;
                client.ErrorOccurred -= OnError;
            }
        }

        public void Apply(HomeSnapshot snapshot)
        {
            _snapshot = snapshot;

            foreach (var room in snapshot.rooms.Values)
            {
                EnsureRoomView(room);
            }

            foreach (var device in snapshot.devices.Values)
            {
                ApplyDevice(device);
            }

            foreach (var person in snapshot.persons.Values)
            {
                ApplyPerson(person);
            }

            ApplyBroadcastHighlight(snapshot);
        }

        private void EnsureRoomView(RoomDto room)
        {
            if (_roomRenderers.ContainsKey(room.id))
            {
                return;
            }

            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = "room-" + room.id;
            go.transform.SetParent(transform, false);
            _roomRenderers[room.id] = go.GetComponent<Renderer>();

            var labelGo = new GameObject("label");
            labelGo.transform.SetParent(go.transform, false);
            labelGo.transform.localPosition = new Vector3(0f, 0.6f, 0f);
            var label = labelGo.AddComponent<TextMesh>();
            label.text = room.name;
            label.characterSize = 0.1f;
            label.anchor = TextAnchor.MiddleCenter;
        }

        private void ApplyDevice(DeviceDto device)
        {
            Transform view;
            if (!_deviceViews.TryGetValue(device.id, out view))
            {
                var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                go.name = "device-" + device.id;
                go.transform.SetParent(transform, false);
                go.transform.localScale = Vector3.one * 0.25f;
                view = go.transform;
                _deviceViews[device.id] = view;

                var labelGo = new GameObject("label");
                labelGo.transform.SetParent(view, false);
                labelGo.transform.localPosition = new Vector3(0f, 0.8f, 0f);
                var label = labelGo.AddComponent<TextMesh>();
                label.characterSize = 0.08f;
                label.anchor = TextAnchor.MiddleCenter;
                _deviceLabels[device.id] = label;
            }

            var renderer = view.GetComponent<Renderer>();
            if (renderer != null)
            {
                if (!device.online)
                {
                    // Offline is visibly different: state must never look live.
                    renderer.material.color = Color.gray;
                }
                else
                {
                    renderer.material.color = device.on
                        ? Color.Lerp(Color.black, Color.white, device.brightness / 100f)
                        : Color.black;
                }
            }

            TextMesh text;
            if (_deviceLabels.TryGetValue(device.id, out text))
            {
                text.text = device.online
                    ? device.name + DescribeState(device)
                    : device.name + "（离线）";
            }
        }

        private static string DescribeState(DeviceDto device)
        {
            if (!device.on)
            {
                return " 关";
            }

            if (device.type == "light")
            {
                return " 开 " + device.brightness + "%";
            }

            if (device.type == "ac")
            {
                return " 开 " + device.mode + " " + device.target_temp.ToString("0.#") + "°";
            }

            return " 开";
        }

        private void ApplyPerson(PersonDto person)
        {
            Transform view;
            if (!_personViews.TryGetValue(person.id, out view))
            {
                var go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                go.name = "person-" + person.id;
                go.transform.SetParent(transform, false);
                go.transform.localScale = new Vector3(0.2f, 0.4f, 0.2f);
                view = go.transform;
                _personViews[person.id] = view;

                var labelGo = new GameObject("label");
                labelGo.transform.SetParent(view, false);
                labelGo.transform.localPosition = new Vector3(0f, 1.2f, 0f);
                var label = labelGo.AddComponent<TextMesh>();
                label.text = person.display_name;
                label.characterSize = 0.08f;
                label.anchor = TextAnchor.MiddleCenter;
                _personLabels[person.id] = label;
            }

            bool localized = HasUsablePosition(person);

            var renderer = view.GetComponent<Renderer>();
            if (renderer != null)
            {
                // Unknown location must look unknown rather than staying put.
                renderer.material.color = localized ? locatedPersonColor : unknownPersonColor;
            }

            TextMesh personLabel;
            if (_personLabels.TryGetValue(person.id, out personLabel))
            {
                personLabel.text = DescribePerson(person, localized);
            }

            if (localized)
            {
                Renderer room;
                if (_roomRenderers.TryGetValue(person.room_id, out room))
                {
                    // x/y are normalized camera-image coordinates, not room-local
                    // offsets, so they are mapped onto the room footprint before
                    // use. See CameraToRoomLocal for the orientation.
                    Vector3 local = CameraToRoomLocal(person.x, person.y, room);
                    view.position = room.transform.position + new Vector3(local.x, personHeight, local.z);
                    return;
                }
            }

            // No known room: park the marker in the unknown staging area instead
            // of leaving it in the room it was last seen in.
            view.position = UnknownStagingPosition(person.id);
        }

        /// <summary>
        /// True when a position is both known and actually localized.
        ///
        /// A room with no coordinates (the old manual-binding payload, where x/y
        /// were null) is not a camera position; drawing the marker at the corner
        /// the mapping would derive from a missing 0.0 would be a fabricated
        /// observation, so those markers go to the staging area too.
        /// </summary>
        private static bool HasUsablePosition(PersonDto person)
        {
            if (!person.HasCameraLocation)
            {
                return false;
            }

            if (person.x < 0f || person.x > 1f || person.y < 0f || person.y > 1f)
            {
                return false;
            }

            // A real observation is never exactly the null-decoded (0,0) corner.
            return person.x != 0f || person.y != 0f;
        }

        /// <summary>
        /// Maps normalized camera coordinates onto a room's floor.
        ///
        /// The service sends x/y in [0,1] image space (x = body-box center, y =
        /// body-box bottom), which is unrelated to the room's own size. The
        /// mapping keeps the person inside the middle 80% of the footprint:
        /// horizontally image-left stays room-left, and image-down (a larger y,
        /// i.e. closer to the camera) maps to the room's front (+Z, towards the
        /// viewer), which is the orientation that reads correctly from the
        /// existing isometric camera. The vertical axis is deliberately untouched:
        /// a marker is never raised or lowered by image position.
        /// </summary>
        private static Vector3 CameraToRoomLocal(float normalizedX, float normalizedY, Renderer room)
        {
            float roomWidth = room == null ? 1f : Mathf.Max(0.5f, room.bounds.size.x);
            float roomDepth = room == null ? 1f : Mathf.Max(0.5f, room.bounds.size.z);

            float localX = (Mathf.Clamp01(normalizedX) - 0.5f) * roomWidth * 0.8f;
            float localZ = (Mathf.Clamp01(normalizedY) - 0.5f) * roomDepth * 0.8f;
            return new Vector3(localX, 0f, localZ);
        }

        /// <summary>
        /// Position of the unknown staging area.
        ///
        /// Derived from the rooms actually present so it stays beside the house if
        /// the layout changes; markers fan out along X so several unknown people
        /// do not stack into one invisible blob.
        /// </summary>
        private Vector3 UnknownStagingPosition(string personId)
        {
            float minX = float.MaxValue;
            float maxX = float.MinValue;
            float sumZ = 0f;
            int count = 0;
            foreach (var pair in _roomRenderers)
            {
                if (pair.Value == null)
                {
                    continue;
                }

                Vector3 position = pair.Value.transform.position;
                minX = Mathf.Min(minX, position.x);
                maxX = Mathf.Max(maxX, position.x);
                sumZ += position.z;
                count++;
            }

            if (count == 0)
            {
                return new Vector3(StagingSlot(personId), unknownStagingHeight, 0f);
            }

            float centerX = (minX + maxX) * 0.5f;
            float averageZ = sumZ / count;
            float spread = Mathf.Max(maxX - minX, 1f) * 0.5f;
            return new Vector3(centerX + StagingSlot(personId) * spread, unknownStagingHeight, averageZ);
        }

        private static int StagingSlot(string personId)
        {
            switch (personId)
            {
                case "dad": return -1;
                case "mom": return 0;
                case "child": return 1;
                default: return 0;
            }
        }

        /// <summary>
        /// Label text for one person.
        ///
        /// A localized person shows 姓名 · 姿态 so the house reflects the camera's
        /// pose estimate. Anyone the applier could not place says so explicitly:
        /// a bare "爸爸" beside a staged marker would imply a position that does
        /// not exist.
        /// </summary>
        private static string DescribePerson(PersonDto person, bool localized)
        {
            string name = string.IsNullOrEmpty(person.display_name) ? person.id : person.display_name;
            if (!localized)
            {
                return name + " · 位置未知";
            }

            return name + " · " + person.PoseLabel;
        }

        private void ApplyBroadcastHighlight(HomeSnapshot snapshot)
        {
            BroadcastDto head = snapshot.HeadBroadcast;
            foreach (var pair in _roomRenderers)
            {
                Renderer renderer = pair.Value;
                bool isHead = head != null && head.room_id == pair.Key;
                renderer.material.color = isHead ? broadcastingColor : idleColor;
            }
        }

        private void OnStatus(string status)
        {
            Debug.Log("[home-service] " + status);
        }

        private void OnError(string error)
        {
            Debug.LogWarning("[home-service] " + error);
        }
    }
}
