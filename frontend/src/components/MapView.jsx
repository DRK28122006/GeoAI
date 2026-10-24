import { useEffect } from "react";
import L from "leaflet";
import { GeoJSON, MapContainer, TileLayer, useMap } from "react-leaflet";

// Mumbai and Pune both fit in this view, the two cities in the problem statement.
const DEFAULT_VIEW = { center: [18.85, 73.35], zoom: 8 };
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Colours mirror the design tokens in styles.css (Leaflet paths can't read CSS vars).
const COLORS = { navy: "#061a33", teal: "#087e78", gold: "#d7a94b", warning: "#b4483c", white: "#ffffff" };

function isBest(props, rec) {
  return rec && props.role === "venue" && props.name === rec.place && props.time === rec.time;
}

// Popup content is built with textContent: venue names come from
// OpenStreetMap and must never be rendered as HTML.
function popupFor(props) {
  const el = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = props.name || "Unnamed place";
  el.append(name);
  const detail = document.createElement("div");
  if (props.role === "origin") detail.textContent = "Starting point";
  if (props.role === "hazard") detail.textContent = `Busy ${props.kind || "road"}: raises local pollution`;
  if (props.role === "venue") {
    const when = props.time
      ? new Date(props.time).toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit" })
      : "";
    detail.textContent = `Score ${Math.round(props.score)} / 100${when ? `, ${when}` : ""}`;
  }
  el.append(detail);
  return el;
}

function FitToData({ geojson }) {
  const map = useMap();
  useEffect(() => {
    if (!geojson?.features?.length) return;
    const bounds = L.geoJSON(geojson).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [48, 48], maxZoom: 15, animate: !reducedMotion() });
  }, [geojson, map]);
  return null;
}

function FlyToFocus({ focus }) {
  const map = useMap();
  useEffect(() => {
    if (!focus) return;
    if (reducedMotion()) map.setView([focus.lat, focus.lon], 16);
    else map.flyTo([focus.lat, focus.lon], 16, { duration: 0.8 });
  }, [focus, map]);
  return null;
}

// The grid column changes width when the sidebar collapses; Leaflet only
// re-measures its container when told to.
function KeepSized() {
  const map = useMap();
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map]);
  return null;
}

export default function MapView({ response, focus }) {
  const geojson = response?.map_geojson;
  const rec = response?.recommendation;
  const hasData = geojson?.features?.length > 0;

  return (
    <main className="map" aria-label="Map">
      <MapContainer {...DEFAULT_VIEW} className="map__canvas">
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={19}
        />
        {hasData && (
          <GeoJSON
            // GeoJSON layers are immutable in react-leaflet; a new key redraws them.
            key={response.request_id || response.data_as_of}
            data={geojson}
            style={() => ({ color: COLORS.warning, weight: 3, opacity: 0.7, dashArray: "6 5" })}
            pointToLayer={(feature, latlng) => {
              const p = feature.properties;
              if (p.role === "origin") {
                return L.circleMarker(latlng, { radius: 7, color: COLORS.white, weight: 2, fillColor: COLORS.navy, fillOpacity: 1 });
              }
              const best = isBest(p, rec);
              return L.circleMarker(latlng, {
                radius: best ? 11 : 7,
                color: best ? COLORS.gold : COLORS.white,
                weight: best ? 4 : 2,
                fillColor: COLORS.teal,
                fillOpacity: best ? 1 : 0.75,
              });
            }}
            onEachFeature={(feature, layer) => layer.bindPopup(popupFor(feature.properties))}
          />
        )}
        <FitToData geojson={hasData ? geojson : null} />
        <FlyToFocus focus={focus} />
        <KeepSized />
      </MapContainer>

      <div className="legend" aria-label="Map legend">
        {hasData ? (
          <ul>
            <li><span className="legend__mark legend__mark--best" />Best pick</li>
            <li><span className="legend__mark legend__mark--venue" />Other places</li>
            <li><span className="legend__mark legend__mark--origin" />Starting point</li>
            <li><span className="legend__mark legend__mark--hazard" />Busy roads</li>
          </ul>
        ) : (
          <p>Places, your starting point and nearby busy roads appear here after you ask.</p>
        )}
      </div>
    </main>
  );
}
