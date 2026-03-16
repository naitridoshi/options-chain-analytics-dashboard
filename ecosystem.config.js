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

      env: {
        PYTHONUNBUFFERED: "1"
      }
    },

  ]
};
