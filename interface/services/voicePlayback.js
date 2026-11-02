const MAX_SEQUENCE_CHARS = 2000;
const MAX_SEQUENCE_SENTENCES = 40;
const ABBREVIATIONS = /(?:^|\s)(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|u\.s|a\.m|p\.m|no|approx|dept|inc|ltd|fig)\.$/i;

function splitSentences(text) {
  const source = String(text || '').trim();
  if (!source) return [];

  const sentences = [];
  let start = 0;
  for (let index = 0; index < source.length; index += 1) {
    if (!'.!?'.includes(source[index])) continue;
    let end = index + 1;
    while (end < source.length && '.!?'.includes(source[end])) end += 1;
    const nextIsBoundary = end === source.length || /\s/.test(source[end]);
    if (!nextIsBoundary) continue;

    const punctuation = source[index];
    const prefix = source.slice(start, end);
    const isDecimal = punctuation === '.' &&
      /\d\.$/.test(prefix) &&
      /^\d/.test(source[end] || '');
    const isInitial = punctuation === '.' && /\b[A-Z]\.$/.test(prefix);
    const isAbbreviation = punctuation === '.' && ABBREVIATIONS.test(prefix);
    const isAcronym = punctuation === '.' && /\b(?:[A-Z]\.){2,}$/.test(prefix);
    if (isDecimal || isInitial || isAbbreviation || isAcronym) {
      index = end - 1;
      continue;
    }

    const sentence = source.slice(start, end).trim();
    if (sentence) sentences.push(sentence);
    start = end;
    while (start < source.length && /\s/.test(source[start])) start += 1;
    index = end - 1;
  }

  const remainder = source.slice(start).trim();
  if (remainder) sentences.push(remainder);
  return sentences;
}

class VoicePlayback {
  constructor({
    client,
    onState = () => {},
    AudioCtor = globalThis.Audio,
    urlApi = globalThis.URL,
    AbortControllerCtor = globalThis.AbortController,
  } = {}) {
    this.client = client;
    this.onState = onState;
    this.AudioCtor = AudioCtor;
    this.urlApi = urlApi;
    this.AbortControllerCtor = AbortControllerCtor;
    this.current = null;
    this.operationId = 0;
    this.activeSequenceId = null;
  }

  async play(text, context = '', { onTiming } = {}) {
    this._cancelCurrent(false);
    const operationId = ++this.operationId;
    this.activeSequenceId = null;
    const result = await this._playChunk(text, context, operationId, onTiming, false);
    return this._publicResult(result);
  }

  async playSequence(text, context = '', { onTiming } = {}) {
    this._cancelCurrent(false);
    const operationId = ++this.operationId;
    this.activeSequenceId = operationId;
    const source = String(text || '').trim();
    if (!source) return this._sequenceError('INVALID_INPUT', 'Speech text is empty.');

    const sentences = splitSentences(source);
    if (
      source.length > MAX_SEQUENCE_CHARS ||
      sentences.length > MAX_SEQUENCE_SENTENCES
    ) {
      this.activeSequenceId = null;
      const fallback = await this._playChunk(source, context, operationId, onTiming, false);
      if (this.operationId === operationId && !fallback.ok) this.activeSequenceId = null;
      return this._publicResult(fallback);
    }
    if (!sentences.length) {
      this.activeSequenceId = null;
      return this._sequenceError('INVALID_INPUT', 'Speech text is empty.');
    }
    if (sentences.length === 1) {
      const result = await this._playChunk(sentences[0], context, operationId, onTiming, false);
      if (this.operationId === operationId) this.activeSequenceId = null;
      return this._publicResult(result);
    }

    let first = await this._playChunk(sentences[0], context, operationId, onTiming, true, true);
    if (
      !first.ok &&
      !first._started &&
      first.status !== 'CANCELLED' &&
      this.operationId === operationId
    ) {
      first = await this._playChunk(source, context, operationId, onTiming, false);
      if (!first.ok) this.activeSequenceId = null;
      else this._finishSequenceWhenDone(first._current, operationId);
      return this._publicResult(first);
    }
    if (!first.ok) {
      if (this.operationId === operationId) this.activeSequenceId = null;
      return this._publicResult(first);
    }

    this._continueSequence(sentences, 1, context, operationId, onTiming, first._current);
    return this._publicResult(first);
  }

  stop(notify = true) {
    const wasActive = Boolean(this.current) || this.activeSequenceId !== null;
    this.operationId += 1;
    this.activeSequenceId = null;
    this._cancelCurrent(false);
    if (notify && wasActive) this._notifyState({ status: 'stopped' });
    return wasActive;
  }

  async _playChunk(text, context, operationId, onTiming, sequenceChunk, deferFailure = false) {
    if (operationId !== this.operationId) {
      return { ok: false, status: 'CANCELLED' };
    }
    const chunk = String(text || '').trim();
    if (!chunk) return { ok: false, status: 'INVALID_INPUT', error: 'Speech text is empty.' };

    let resolveCompletion;
    const current = {
      operationId,
      sequenceChunk,
      deferFailure,
      controller: this.AbortControllerCtor ? new this.AbortControllerCtor() : null,
      audio: null,
      url: null,
      started: false,
      settled: false,
      completion: new Promise((resolve) => { resolveCompletion = resolve; }),
      resolveCompletion: null,
    };
    current.resolveCompletion = resolveCompletion;
    this.current = current;
    this._notifyState({ status: 'generating' });

    let result;
    try {
      result = await this.client.speak(
        chunk,
        'normal',
        {
          signal: current.controller ? current.controller.signal : undefined,
          context,
          onTiming,
        },
      );
    } catch (error) {
      result = { ok: false, status: 'ERROR', error: String(error && error.message) };
    }
    if (operationId !== this.operationId || this.current !== current) {
      this._settle(current, { ok: false, status: 'CANCELLED' });
      return { ok: false, status: 'CANCELLED' };
    }
    if (!result || !result.ok || !result.audio) {
      this.current = null;
      const failure = result || {
        ok: false,
        status: 'INVALID_AUDIO',
        error: 'Speech service returned no audio.',
      };
      this._settle(current, failure);
      if (!deferFailure) {
        this._notifyState({
          status: failure.status === 'CANCELLED' ? 'stopped' : 'error',
          message: failure.error || 'Speech service returned no audio.',
        });
      }
      return { ...failure, _started: current.started };
    }

    try {
      current.url = this.urlApi.createObjectURL(result.audio);
      current.audio = new this.AudioCtor(current.url);
      current.audio.addEventListener('playing', () => {
        if (this.current !== current || current.started) return;
        current.started = true;
        onTiming?.('playback_started');
        this._notifyState({
          status: 'playing',
          engine: result.engine,
          isClone: result.is_clone,
        });
      }, { once: true });
      current.audio.addEventListener('ended', () => {
        this._finish(current, 'finished');
        this._settle(current, { ok: true, status: 'finished' });
      }, { once: true });
      current.audio.addEventListener('error', () => {
        this._finish(current, 'error', 'Audio playback failed.');
        this._settle(current, { ok: false, status: 'PLAYBACK_ERROR', error: 'Audio playback failed.' });
      }, { once: true });
      await current.audio.play();
      if (operationId !== this.operationId) {
        return { ok: false, status: 'CANCELLED' };
      }
      if (this.current !== current) {
        if (current.settled && current.completionResult.ok) {
          return {
            ok: true,
            status: 'finished',
            engine: result.engine,
            is_clone: result.is_clone,
            _current: current,
            _started: current.started,
          };
        }
        if (current.settled) {
          return { ...current.completionResult, _started: current.started };
        }
        return { ok: false, status: 'CANCELLED' };
      }
      if (!current.started) {
        current.started = true;
        onTiming?.('playback_started');
        this._notifyState({
          status: 'playing',
          engine: result.engine,
          isClone: result.is_clone,
        });
      }
      return {
        ok: true,
        status: 'playing',
        engine: result.engine,
        is_clone: result.is_clone,
        _current: current,
        _started: current.started,
      };
    } catch (error) {
      if (this.current === current) {
        this._release(current);
        this.current = null;
      }
      const failure = { ok: false, status: 'PLAYBACK_ERROR', error: String(error && error.message) };
      this._settle(current, failure);
      if (!deferFailure) this._notifyState({ status: 'error', message: failure.error });
      return { ...failure, _started: current.started };
    }
  }

  async _continueSequence(sentences, index, context, operationId, onTiming, current) {
    let active = current;
    while (index < sentences.length && operationId === this.operationId) {
      const ended = await active.completion;
      if (operationId !== this.operationId || !ended.ok) {
        if (operationId === this.operationId) this.activeSequenceId = null;
        return;
      }
      const next = await this._playChunk(
        sentences[index],
        context,
        operationId,
        onTiming,
        true,
      );
      if (!next.ok) {
        if (operationId === this.operationId) this.activeSequenceId = null;
        return;
      }
      active = next._current;
      index += 1;
    }

    if (operationId === this.operationId) {
      const ended = await active.completion;
      if (operationId === this.operationId && ended.ok) {
        this.activeSequenceId = null;
        this._notifyState({ status: 'finished' });
      } else if (operationId === this.operationId) {
        this.activeSequenceId = null;
      }
    }
  }

  async _finishSequenceWhenDone(current, operationId) {
    const ended = await current.completion;
    if (operationId === this.operationId && ended.ok) {
      this.activeSequenceId = null;
      this._notifyState({ status: 'finished' });
    } else if (operationId === this.operationId) {
      this.activeSequenceId = null;
    }
  }

  _sequenceError(status, error) {
    this._notifyState({ status: 'error', message: error });
    return { ok: false, status, error };
  }

  _publicResult(result) {
    if (!result) return result;
    const { _current, _started, ...publicResult } = result;
    return publicResult;
  }

  _settle(current, result) {
    if (current.settled) return;
    current.settled = true;
    current.completionResult = result;
    current.resolveCompletion(result);
  }

  _finish(current, status, message) {
    if (this.current !== current) return;
    this._release(current);
    this.current = null;
    if ((status === 'error' && !current.deferFailure) || !current.sequenceChunk) {
      this._notifyState({ status, message });
    }
  }

  _cancelCurrent(notify) {
    const current = this.current;
    if (!current) return;
    this.current = null;
    if (current.controller) current.controller.abort();
    this._release(current);
    this._settle(current, { ok: false, status: 'CANCELLED' });
    if (notify) this._notifyState({ status: 'stopped' });
  }

  _notifyState(state) {
    try {
      this.onState(state);
    } catch {
      // Ignore callback failures so playback control remains isolated from UI/state rendering errors.
    }
  }

  _release(current) {
    if (current.audio) {
      current.audio.pause();
      current.audio.removeAttribute('src');
      current.audio.load();
      current.audio = null;
    }
    if (current.url) {
      this.urlApi.revokeObjectURL(current.url);
      current.url = null;
    }
  }
}

export { VoicePlayback, splitSentences };
