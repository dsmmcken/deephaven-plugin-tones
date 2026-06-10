import { type ElementPlugin, PluginType } from '@deephaven/plugin';
import DeephavenPluginTonesView from './DeephavenPluginTonesView';

// Register the plugin with Deephaven
export const DeephavenPluginTonesPlugin: ElementPlugin = {
  // The name of the plugin
  name: 'deephaven-plugin-tones',
  // The type of plugin - this will generally be ELEMENT_PLUGIN
  type: PluginType.ELEMENT_PLUGIN,
  // The mapping of names to React elements for the plugin. This should match the value returned by `name`
  // in deephaven_plugin_tones_component in deephaven_plugin_tones_component.py
  mapping: {
    'deephaven_plugin_tones.deephaven_plugin_tones_component':
      DeephavenPluginTonesView,
  },
};

export default DeephavenPluginTonesPlugin;
