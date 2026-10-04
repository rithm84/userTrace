/**
 * Structured diagnostic logging for the SDK itself.
 *
 * The SDK never collects the client's logs. These records describe uTrace's own
 * operation, carry structured fields instead of interpolated message strings,
 * and go to stderr so they cannot be mistaken for product telemetry.
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export type LogFieldValue = string | number | boolean;

export interface LogFields {
  readonly [field: string]: LogFieldValue;
}

/** Sink a log record is written to. Injected so tests can capture records. */
export interface LogSink {
  write(record: string): void;
}

interface LogRecord {
  readonly timestamp: string;
  readonly level: LogLevel;
  readonly component: string;
  readonly event: string;
  readonly fields: LogFields;
}

const LEVEL_ORDER: Readonly<Record<LogLevel, number>> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

/**
 * Emits JSON records at or above `minimumLevel`.
 *
 * `component` names the SDK module; `event` is a stable snake_case event name.
 * Dynamic values belong in `fields`, never in `event`.
 */
export class StructuredLogger {
  private readonly sink: LogSink;
  private readonly minimumLevel: LogLevel;
  private readonly component: string;

  public constructor(component: string, minimumLevel: LogLevel, sink: LogSink) {
    this.component = component;
    this.minimumLevel = minimumLevel;
    this.sink = sink;
  }

  /** Returns a logger for a nested component, sharing sink and level. */
  public child(component: string): StructuredLogger {
    return new StructuredLogger(`${this.component}.${component}`, this.minimumLevel, this.sink);
  }

  public debug(event: string, fields: LogFields): void {
    this.emit('debug', event, fields);
  }

  public info(event: string, fields: LogFields): void {
    this.emit('info', event, fields);
  }

  public warn(event: string, fields: LogFields): void {
    this.emit('warn', event, fields);
  }

  public error(event: string, fields: LogFields): void {
    this.emit('error', event, fields);
  }

  private emit(level: LogLevel, event: string, fields: LogFields): void {
    if (LEVEL_ORDER[level] < LEVEL_ORDER[this.minimumLevel]) {
      return;
    }
    const record: LogRecord = {
      timestamp: new Date().toISOString(),
      level,
      component: this.component,
      event,
      fields,
    };
    this.sink.write(`${JSON.stringify(record)}\n`);
  }
}

/** Sink writing to the process's standard error stream. */
export const stderrSink: LogSink = {
  write(record: string): void {
    process.stderr.write(record);
  },
};
