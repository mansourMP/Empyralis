// Empyralis Mini-App SDK
// Lightweight client for hosted mini-apps running inside the Empyralis workspace shell.
// Usage: import { EmpyralisApp } from './mini-app-sdk.js';
(function (global) {
  'use strict';

  var MESSAGE_TYPE_READY = 'empyralis.hosted_app.bridge.ready';
  var MESSAGE_TYPE_REQUEST = 'empyralis.hosted_app.bridge.request';
  var MESSAGE_TYPE_RESPONSE = 'empyralis.hosted_app.bridge.response';

  var _origin = '*';
  var _target = null; // parent window
  var _runtime = {};
  var _pending = {};
  var _listeners = {};
  var _ready = false;
  var _readyCallbacks = [];
  var _seq = 0;

  function _log(level, msg) {
    if (typeof console !== 'undefined' && console[level]) {
      console[level]('[Empyralis SDK] ' + msg);
    }
  }

  function _post(msg) {
    if (_target) {
      _target.postMessage(msg, _origin);
    }
  }

  function _onMessage(event) {
    if (!event || !event.data || typeof event.data !== 'object') return;
    var data = event.data;
    if (data.type === MESSAGE_TYPE_RESPONSE && data.id) {
      var resolver = _pending[data.id];
      if (resolver) {
        delete _pending[data.id];
        if (data.error) {
          resolver.reject(new Error(data.error));
        } else {
          resolver.resolve(data.payload || {});
        }
      }
    }
    if (data.type && _listeners[data.type]) {
      var cbs = _listeners[data.type].slice();
      for (var i = 0; i < cbs.length; i++) {
        try { cbs[i](data.payload || data); } catch (e) { /* swallow */ }
      }
    }
  }

  function _send(kind, payload) {
    return new Promise(function (resolve, reject) {
      var id = 'emp_' + (++_seq) + '_' + Date.now();
      _pending[id] = { resolve: resolve, reject: reject };
      _post({ type: MESSAGE_TYPE_REQUEST, id: id, kind: kind, payload: payload || {} });
      setTimeout(function () {
        if (_pending[id]) {
          delete _pending[id];
          reject(new Error('Bridge request timed out: ' + kind));
        }
      }, 15_000);
    });
  }

  var EmpyralisApp = {
    VERSION: '1.0.0',

    // --- Lifecycle ---

    ready: function () {
      _target = window.parent;
      window.addEventListener('message', _onMessage, false);
      _post({ type: MESSAGE_TYPE_READY });
      _ready = true;
      var cbs = _readyCallbacks.slice();
      _readyCallbacks = [];
      for (var i = 0; i < cbs.length; i++) cbs[i]();
    },

    onReady: function (fn) {
      if (_ready) { fn(); return; }
      _readyCallbacks.push(fn);
    },

    // --- Data ---

    getRuntime: function () {
      return _send('runtime.read', {});
    },

    readRecords: function (filters) {
      return _send('records.retrieve', filters || {});
    },

    writeRecord: function (record) {
      return _send('records.write', record || {});
    },

    getSummary: function () {
      return _send('summary.read', {});
    },

    // --- AI ---

    invokeAI: function (prompt, options) {
      return _send('ai.invoke', { prompt: prompt, options: options || {} });
    },

    // --- Sage bridge ---

    requestSage: function (payload) {
      return _send('bridge.sage.request', payload || {});
    },

    // --- Events ---

    on: function (eventType, fn) {
      if (!_listeners[eventType]) _listeners[eventType] = [];
      _listeners[eventType].push(fn);
    },

    off: function (eventType, fn) {
      if (!_listeners[eventType]) return;
      _listeners[eventType] = _listeners[eventType].filter(function (f) { return f !== fn; });
    },
  };

  // Expose
  global.EmpyralisApp = EmpyralisApp;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = EmpyralisApp;
  }
})(typeof window !== 'undefined' ? window : globalThis);
