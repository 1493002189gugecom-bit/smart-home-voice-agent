using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace SmartHome
{
    /// <summary>
    /// Draws the vision overlay (body boxes and COCO skeletons) inside the
    /// preview widget.
    ///
    /// Implemented as a <see cref="MaskableGraphic"/> so the overlay lives in the
    /// same canvas as the RawImage and shares its vertex pipeline. Two deliberate
    /// choices:
    ///
    /// * <c>raycastTarget</c> is disabled, because the overlay must never eat
    ///   clicks meant for the panel's buttons;
    /// * the mesh is rebuilt only when the tracks or the layout actually change,
    ///   not per frame, because uGUI regenerates the whole canvas whenever a
    ///   graphic dirties itself.
    ///
    /// Camera coordinates are normalized to the source frame, while the widget can
    /// be any shape, so every point is mapped through the RawImage's aspect-fit
    /// rectangle. Without that step the overlay would drift as soon as the panel
    /// was resized to a different aspect than the camera.
    /// </summary>
    [RequireComponent(typeof(CanvasRenderer))]
    public sealed class VisionOverlayGraphic : MaskableGraphic
    {
        /// <summary>COCO 17-keypoint skeleton, as index pairs.</summary>
        private static readonly int[] SkeletonEdges =
        {
            0, 1, 0, 2, 1, 3, 2, 4,
            5, 6, 5, 7, 7, 9, 6, 8, 8, 10,
            5, 11, 6, 12, 11, 12, 11, 13, 13, 15, 12, 14, 14, 16,
        };

        public const int CocoKeypointCount = 17;

        [Tooltip("Source widget the normalized coordinates are mapped onto.")]
        public RawImage preview;

        [Tooltip("Thickness of a body rectangle in pixels.")]
        public float boxThickness = 2f;

        [Tooltip("Thickness of a skeleton edge in pixels.")]
        public float skeletonThickness = 2f;

        [Tooltip("Radius of a keypoint dot in pixels.")]
        public float jointRadius = 2.5f;

        [Tooltip("Keypoints below this confidence are not drawn.")]
        public float keypointConfidenceThreshold = 0.35f;

        public Color confirmedColor = new Color(0.25f, 0.9f, 0.45f, 0.95f);
        public Color candidateColor = new Color(1f, 0.8f, 0.25f, 0.9f);
        public Color unknownColor = new Color(0.95f, 0.35f, 0.35f, 0.9f);
        public Color skeletonColor = new Color(0.35f, 0.85f, 1f, 0.85f);

        private readonly List<VisionTrackDto> _tracks = new List<VisionTrackDto>();

        /// <summary>Replaces the drawn tracks and requests one mesh rebuild.</summary>
        public void SetTracks(List<VisionTrackDto> tracks)
        {
            _tracks.Clear();
            if (tracks != null)
            {
                for (int i = 0; i < tracks.Count; i++)
                {
                    VisionTrackDto track = tracks[i];
                    if (track == null || !track.bbox_valid)
                    {
                        // A track without a box has nothing to place; drawing a
                        // guess would put a box on a person who is not there.
                        continue;
                    }

                    _tracks.Add(track);
                }
            }

            SetVerticesDirty();
        }

        /// <summary>Clears the overlay, e.g. on disconnect or a session change.</summary>
        public void Clear()
        {
            _tracks.Clear();
            SetVerticesDirty();
        }

        protected override void OnEnable()
        {
            base.OnEnable();
            raycastTarget = false;
        }

        protected override void OnRectTransformDimensionsChange()
        {
            base.OnRectTransformDimensionsChange();
            // A resize changes the aspect-fit rectangle, so the cached mesh is
            // no longer aligned and must be regenerated.
        }

#if UNITY_EDITOR
        /// <summary>
        /// Keeps the inspector-visible flag honest: the overlay must never be a
        /// raycast target, whatever the serialized value says.
        /// </summary>
        protected override void OnValidate()
        {
            base.OnValidate();
            raycastTarget = false;
        }
#endif

        protected override void OnPopulateMesh(VertexHelper vh)
        {
            // VertexHelper is fresh for every rebuild, including rebuilds caused
            // by the parent canvas, so the geometry must always be repopulated.
            vh.Clear();

            Rect rect = FitRect();
            if (rect.width <= 0f || rect.height <= 0f)
            {
                return;
            }

            Color32 skeleton = skeletonColor;
            for (int t = 0; t < _tracks.Count; t++)
            {
                VisionTrackDto track = _tracks[t];
                Color32 color = ColorFor(track);

                if (track.keypoints_valid && track.keypointCount > 0)
                {
                    AddSkeleton(vh, track, rect, skeleton);
                }

                AddBox(vh, track, rect, color);
            }
        }

        /// <summary>
        /// Maps a normalized source coordinate onto the widget.
        ///
        /// Returns false when no source rectangle is known, in which case the
        /// caller must not draw: an overlay positioned by a guess would be worse
        /// than no overlay.
        /// </summary>
        public bool TryMapNormalized(Vector2 source, out Vector2 local)
        {
            local = Vector2.zero;
            Rect rect = FitRect();
            if (rect.width <= 0f || rect.height <= 0f)
            {
                return false;
            }

            local = new Vector2(rect.xMin + source.x * rect.width, rect.yMax - source.y * rect.height);
            return true;
        }

        /// <summary>
        /// The aspect-fit rectangle of the preview inside this graphic.
        ///
        /// The RawImage's own rect may be clipped by an AspectRatioFitter; when
        /// that fitter is present its rect is the truthful target, so it is
        /// preferred over computing an approximation here.
        /// </summary>
        public Rect FitRect()
        {
            RectTransform self = rectTransform;
            if (self == null)
            {
                return new Rect(0f, 0f, 0f, 0f);
            }

            Rect area = self.rect;
            if (area.width <= 0f || area.height <= 0f)
            {
                return new Rect(0f, 0f, 0f, 0f);
            }

            if (preview == null)
            {
                return area;
            }

            var fitter = preview.GetComponent<AspectRatioFitter>();
            if (fitter != null && fitter.aspectMode != AspectRatioFitter.AspectMode.None)
            {
                Rect fitted = preview.rectTransform.rect;
                if (fitted.width > 0f && fitted.height > 0f)
                {
                    return new Rect(
                        area.center.x - fitted.width * 0.5f,
                        area.center.y - fitted.height * 0.5f,
                        fitted.width,
                        fitted.height
                    );
                }
            }

            float sourceAspect = SourceAspect();
            float areaAspect = area.width / area.height;
            if (sourceAspect <= 0f || Mathf.Approximately(sourceAspect, areaAspect))
            {
                return area;
            }

            if (sourceAspect > areaAspect)
            {
                float height = area.width / sourceAspect;
                return new Rect(
                    area.xMin, area.center.y - height * 0.5f, area.width, height
                );
            }

            float width = area.height * sourceAspect;
            return new Rect(
                area.center.x - width * 0.5f, area.yMin, width, area.height
            );
        }

        private Color32 ColorFor(VisionTrackDto track)
        {
            if (track.IsConfirmed)
            {
                return confirmedColor;
            }

            // A candidate is not yet a name, so it must not look like one.
            return track.identity_state == VisionIdentity.Candidate ||
                   track.identity_state == VisionIdentity.Held
                ? candidateColor
                : unknownColor;
        }

        private void AddBox(VertexHelper vh, VisionTrackDto track, Rect rect, Color32 color)
        {
            float x0 = Mathf.Clamp01(track.bbox[0]);
            float y0 = Mathf.Clamp01(track.bbox[1]);
            float x1 = Mathf.Clamp01(track.bbox[2]);
            float y1 = Mathf.Clamp01(track.bbox[3]);

            // Image space grows downward; UI space grows upward, so Y is flipped
            // exactly once, here.
            float left = rect.xMin + x0 * rect.width;
            float right = rect.xMin + x1 * rect.width;
            float top = rect.yMax - y0 * rect.height;
            float bottom = rect.yMax - y1 * rect.height;
            if (right - left < 1f || top - bottom < 1f)
            {
                return;
            }

            AddLine(vh, new Vector2(left, top), new Vector2(right, top), boxThickness, color);
            AddLine(vh, new Vector2(right, top), new Vector2(right, bottom), boxThickness, color);
            AddLine(vh, new Vector2(right, bottom), new Vector2(left, bottom), boxThickness, color);
            AddLine(vh, new Vector2(left, bottom), new Vector2(left, top), boxThickness, color);
        }

        private void AddSkeleton(VertexHelper vh, VisionTrackDto track, Rect rect, Color32 color)
        {
            for (int i = 0; i < SkeletonEdges.Length; i += 2)
            {
                int a = SkeletonEdges[i];
                int b = SkeletonEdges[i + 1];
                if (a >= track.keypointCount || b >= track.keypointCount)
                {
                    // Partially reported skeletons still draw the edges that exist.
                    continue;
                }

                if (track.KeypointConfidence(a) < keypointConfidenceThreshold ||
                    track.KeypointConfidence(b) < keypointConfidenceThreshold)
                {
                    continue;
                }

                Vector2 from;
                Vector2 to;
                if (!TryMapNormalized(new Vector2(track.KeypointX(a), track.KeypointY(a)), out from) ||
                    !TryMapNormalized(new Vector2(track.KeypointX(b), track.KeypointY(b)), out to))
                {
                    continue;
                }

                AddLine(vh, from, to, skeletonThickness, color);
            }

            for (int k = 0; k < track.keypointCount && k < CocoKeypointCount; k++)
            {
                if (track.KeypointConfidence(k) < keypointConfidenceThreshold)
                {
                    continue;
                }

                Vector2 center;
                if (!TryMapNormalized(new Vector2(track.KeypointX(k), track.KeypointY(k)), out center))
                {
                    continue;
                }

                AddQuad(vh, new Vector2(center.x - jointRadius, center.y - jointRadius),
                    new Vector2(center.x + jointRadius, center.y + jointRadius), color);
            }
        }

        /// <summary>Adds one line as a screen-space quad so thickness is in pixels.</summary>
        private static void AddLine(VertexHelper vh, Vector2 from, Vector2 to, float thickness, Color32 color)
        {
            Vector2 delta = to - from;
            if (delta.sqrMagnitude < 0.0001f)
            {
                return;
            }

            Vector2 normal = new Vector2(-delta.y, delta.x).normalized * Mathf.Max(0.5f, thickness * 0.5f);
            int start = vh.currentVertCount;
            vh.AddVert(from - normal, color, Vector2.zero);
            vh.AddVert(from + normal, color, Vector2.zero);
            vh.AddVert(to + normal, color, Vector2.zero);
            vh.AddVert(to - normal, color, Vector2.zero);
            vh.AddTriangle(start, start + 1, start + 2);
            vh.AddTriangle(start, start + 2, start + 3);
        }

        private static void AddQuad(VertexHelper vh, Vector2 min, Vector2 max, Color32 color)
        {
            int start = vh.currentVertCount;
            vh.AddVert(new Vector2(min.x, min.y), color, Vector2.zero);
            vh.AddVert(new Vector2(min.x, max.y), color, Vector2.zero);
            vh.AddVert(new Vector2(max.x, max.y), color, Vector2.zero);
            vh.AddVert(new Vector2(max.x, min.y), color, Vector2.zero);
            vh.AddTriangle(start, start + 1, start + 2);
            vh.AddTriangle(start, start + 2, start + 3);
        }

        private float SourceAspect()
        {
            if (preview != null && preview.texture != null && preview.texture.height > 0)
            {
                return (float)preview.texture.width / preview.texture.height;
            }

            return -1f;
        }
    }
}
