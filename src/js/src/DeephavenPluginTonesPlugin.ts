import { type ElementPlugin, PluginType } from "@deephaven/plugin";
import { handleToneEvent, TONES_EVENT } from "./ToneEngine";

// This plugin provides no elements, only an event handler: the Python side
// sends events with `use_send_event` and the handler below plays them. This is
// the same mechanism as built-in deephaven.ui events (namespaced
// `deephaven.ui.*`); plugins namespace their own events with their package
// namespace to avoid collisions.
type ElementPluginWithEvents = ElementPlugin & {
  eventMapping: Record<string, (params: Record<string, unknown>) => void>;
};

// Register the plugin with Deephaven
export const DeephavenPluginTonesPlugin: ElementPluginWithEvents = {
  // The name of the plugin
  name: "deephaven-plugin-tones",
  // The type of plugin - this will generally be ELEMENT_PLUGIN
  type: PluginType.ELEMENT_PLUGIN,
  // No elements of its own — tones are triggered by events, not mounted.
  mapping: {},
  // The event name must match TONES_EVENT in the Python package's _config.py.
  eventMapping: {
    [TONES_EVENT]: (params: Record<string, unknown>) => {
      handleToneEvent(params);
    },
  },
};

export default DeephavenPluginTonesPlugin;
