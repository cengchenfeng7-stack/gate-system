import * as Sentry from "@sentry/node";

Sentry.init({
  dsn: "https://dcab3698f2103d522f91a3a53aab74f5@o4512247437131776.ingest.de.sentry.io/4512247504568400",
  environment: process.env.NODE_ENV || "development",
  tracesSampleRate: 0.1,
});
