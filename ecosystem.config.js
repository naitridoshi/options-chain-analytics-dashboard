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
  ]
};
