module.exports = {
  apps: [

    {
      name: "options-dashboard-api",

      script: "apps/fastapi/src/__main__.py",

      interpreter: ".venv/bin/python",

      instances: 1,
      exec_mode: "fork",

      autorestart: true,
      watch: false,

      max_memory_restart: "1G",

      kill_timeout: 5000,
      listen_timeout: 10000,
      wait_ready: false,

      env: {
        PYTHONUNBUFFERED: "1"
      }
    },

    {
      name: "options-dashboard-scheduler",

      script: "apps/scheduler/src/__main__.py",

      interpreter: ".venv/bin/python",

      instances: 1,
      exec_mode: "fork",

      autorestart: true,
      watch: false,

      max_memory_restart: "1G",

      kill_timeout: 5000,
      listen_timeout: 10000,
      wait_ready: false,

      env: {
        PYTHONUNBUFFERED: "1"
      }
    },

    {
      name: "options-dashboard-live-market-data",

      script: "apps/live_market_data/src/__main__.py",

      interpreter: ".venv/bin/python",

      instances: 1,
      exec_mode: "fork",

      autorestart: true,
      watch: false,

      max_memory_restart: "1G",

      kill_timeout: 5000,
      listen_timeout: 10000,
      wait_ready: false,

      env: {
        PYTHONUNBUFFERED: "1"
      }
    },

  ],

  // Run before any app starts to kill orphaned processes from previous PM2 runs
  // These zombies hold Redis connections in CLOSE_WAIT indefinitely
  deploy: {
    production: {
      "pre-setup": "bash scripts/kill_orphans.sh --force"
    }
  }
};
