import { useEffect, useRef, useState, type FormEvent } from "react";
import { searchPlaces } from "../api";
import { nextLocalHour } from "../format";
import type { Place, PlanRequest } from "../types";

interface PlaceInputProps {
  id: string;
  label: string;
  placeholder: string;
  value: Place | null;
  onChange: (place: Place | null) => void;
}

function PlaceInput({ id, label, placeholder, value, onChange }: PlaceInputProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (value) setQuery(value.label);
  }, [value]);

  useEffect(() => {
    const text = query.trim();
    setResults([]);
    if (text.length < 2 || value?.label === query) {
      setSearching(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      searchPlaces(text, controller.signal)
        .then((places) => {
          setResults(places);
          setOpen(places.length > 0);
        })
        .catch(() => {
          /* a failed type-ahead must never block planning */
        })
        .finally(() => setSearching(false));
    }, 280);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, value]);

  useEffect(() => {
    function onDocumentClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    return () => document.removeEventListener("mousedown", onDocumentClick);
  }, []);

  return (
    <div className="field" ref={containerRef}>
      <label htmlFor={id}>{label}</label>
      <div className="place-input">
        <input
          id={id}
          type="text"
          autoComplete="off"
          placeholder={placeholder}
          value={query}
          aria-expanded={open}
          onChange={(event) => {
            setQuery(event.target.value);
            if (value && event.target.value !== value.label) onChange(null);
            setOpen(true);
          }}
          onFocus={() => setOpen(results.length > 0)}
        />
        {searching && <span className="place-input__spinner" aria-hidden="true" />}
        {open && results.length > 0 && (
          <ul className="place-results" role="listbox">
            {results.map((place) => (
              <li key={`${place.label}-${place.lat}-${place.lon}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  onClick={() => {
                    onChange(place);
                    setQuery(place.label);
                    setOpen(false);
                  }}
                >
                  <span>{place.label}</span>
                  <small>{place.kind}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface PlanFormProps {
  loading: boolean;
  onPlan: (request: PlanRequest) => void;
}

const LOAD_PRESETS = [0, 20_000, 44_000, 80_000];

export function PlanForm({ loading, onPlan }: PlanFormProps) {
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [departure, setDeparture] = useState(() => nextLocalHour(2));
  const [load, setLoad] = useState("44000");
  const [intervalMiles, setIntervalMiles] = useState(25);
  const [formError, setFormError] = useState<string | null>(null);

  const loadLbs = Number(load);
  const ready =
    origin !== null &&
    destination !== null &&
    Number.isFinite(loadLbs) &&
    loadLbs >= 0 &&
    load.trim() !== "";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!origin || !destination) {
      setFormError("Choose both a starting point and a destination from the suggestions.");
      return;
    }
    if (!Number.isFinite(loadLbs) || loadLbs < 0) {
      setFormError("Enter the loaded weight in pounds.");
      return;
    }
    setFormError(null);
    const departureDate = new Date(departure);
    if (Number.isNaN(departureDate.getTime())) {
      setFormError("Choose a departure date and time.");
      return;
    }

    onPlan({
      origin: { lat: origin.lat, lon: origin.lon, label: origin.label },
      destination: { lat: destination.lat, lon: destination.lon, label: destination.label },
      departureAt: departureDate.toISOString(),
      loadLbs,
      intervalMiles,
    });
  }

  return (
    <form className="plan-form" onSubmit={submit}>
      <PlaceInput
        id="origin"
        label="From"
        placeholder="Denver, Colorado"
        value={origin}
        onChange={setOrigin}
      />
      <PlaceInput
        id="destination"
        label="To"
        placeholder="Kansas City, Missouri"
        value={destination}
        onChange={setDestination}
      />

      <div className="field-row">
        <div className="field">
          <label htmlFor="departure">Departure</label>
          <input
            id="departure"
            type="datetime-local"
            value={departure}
            onChange={(event) => setDeparture(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="load">Load weight (lb)</label>
          <input
            id="load"
            type="number"
            inputMode="numeric"
            min={0}
            max={120000}
            step={500}
            value={load}
            onChange={(event) => setLoad(event.target.value)}
          />
        </div>
      </div>

      <div className="presets" role="group" aria-label="Common load weights">
        {LOAD_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className={Number(load) === preset ? "chip chip--active" : "chip"}
            onClick={() => setLoad(String(preset))}
          >
            {preset === 0 ? "Empty" : `${preset / 1000}k`}
          </button>
        ))}
      </div>

      <div className="field">
        <label>Weather sampling</label>
        <div className="segmented" role="group" aria-label="Weather sampling interval">
          {[10, 25, 50].map((miles) => (
            <button
              key={miles}
              type="button"
              aria-pressed={intervalMiles === miles}
              className={
                intervalMiles === miles ? "segmented__option is-active" : "segmented__option"
              }
              onClick={() => setIntervalMiles(miles)}
            >
              {miles} mi
            </button>
          ))}
        </div>
      </div>

      {formError && <p className="form-error">{formError}</p>}

      <button className="primary-button" type="submit" disabled={loading || !ready}>
        {loading ? "Planning…" : "Compare routes"}
      </button>

      <p className="hint">
        Weather is sampled at your chosen interval using each checkpoint's estimated arrival
        time.
      </p>
    </form>
  );
}
