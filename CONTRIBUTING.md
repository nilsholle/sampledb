# Contributing to SampleDB

SampleDB is open source and we hope that ideas and improvements from different fields and institutions will help to make it as useful as possible. Thank you for considering contributing to SampleDB!

## Reporting Issues

- Describe the situation to help us reproduce it. This might include providing relevant schemas and/or objects as JSON files, scripts for WebAPI issues and/or a modified `sampledb/scripts/set_up_demo.py` that can recreate the situation.
- Describe what you expected to happen.
- Describe what actually happened. Please include logs and screenshots or screen recordings for UI issues.
- List your SampleDB version. If possible, try to reproduce your issue using the current `develop` branch.

## Setting up a Development Installation

### Using virtualenv

- To work on SampleDB you will need a PostgreSQL database. On Linux, your distribution will likely have a package for this and on macOS, you can use [Postgres.app](https://postgresapp.com/).
- Set up a virtual environment using `python3 -m venv env` and activate it using `source env/bin/activate`
- Install the requirements, using `pip install -r requirements.txt`
- Set [configuration environment variables](https://scientific-it-systems.iffgit.fz-juelich.de/SampleDB/developer_guide/configuration.html). At the very least you will need to set a mail server and sender, e.g. by using `export SAMPLEDB_MAIL_SERVER=mail.example.com`, `export SAMPLEDB_MAIL_SENDER=sampledb@example.com` and `export SAMPLEDB_CONTACT_EMAIL=sampledb@example.com`. Depending on how you set up your database, you may have to set the `SAMPLEDB_SQLALCHEMY_DATABASE_URI`.
- Start an instance using demo data from the `set_up_demo` script, using `python demo.py`. This way, you will have some example instruments, actions, objects and users. If you try to access a route that requires a user account, you will automatically be signed in.

### Using docker with docker-compose

If you're using a docker-compose workflow, simply add a volume line to mount the `sampledb` folder in the container:

~~~yaml
volumes:
  - ./files:/home/sampledb/files
  # only for dev
  - ./sampledb:/home/sampledb/sampledb
~~~

Also uncomment the `FLASK_ENV=development` environment variable in the compose file.

If you want to populate the database with fake data, you can use the `set_up_demo` script on an empty database:

~~~bash
alias sdb="docker exec -it sampledb sampledb"
sdb set_up_demo
~~~


## Translating SampleDB

- SampleDB was primarily developed in English, but can be translated using [Babel](http://babel.pocoo.org/en/latest/cmdline.html) and the *gettext* internationalization and localization system.
- A new translation can be created using `pybabel extract -F babel.cfg -k lazy_gettext -o sampledb/messages.pot sampledb` followed by `pybabel init -i sampledb/messages.pot -d sampledb/translations -D extracted_messages -l <locale>`.
- The locale for this translation also needs to be added to `sampledb.logic.locales.SUPPORTED_LOCALES`, so users will be able to select it.
- Additionally, a language should be created for it, so user-generated content can be translated as well.
- If you would like to translate SampleDB to a different language, please open up an [issue](https://github.com/sciapp/sampledb/issues/new/choose) to coordinate with us.

## Submitting Changes

- We aim to support a wide variety of use cases from different fields. Please keep this in mind and prefer generic solutions to specialized ones.
- Adhere to the code style. You can check this using `python3 -m pycodestyle --ignore=E402,E501,W504 sampledb` and `python3 -m pyflakes sampledb`.
- Include tests if your change introduces a new feature or fixes a bug. Make sure that the test fails
  without your change. You can run all tests using `python3 -m pytest -s --cov=sampledb/ tests`. As the tests require an LDAP server, you will need to set the following [configuration environment variables](https://scientific-it-systems.iffgit.fz-juelich.de/SampleDB/developer_guide/configuration.html):
  - `SAMPLEDB_LDAP_SERVER`
  - `SAMPLEDB_LDAP_USER_BASE_DN`
  - `SAMPLEDB_LDAP_UID_FILTER`
  - `SAMPLEDB_LDAP_NAME_ATTRIBUTE`
  - `SAMPLEDB_LDAP_MAIL_ATTRIBUTE`
  - `SAMPLEDB_LDAP_OBJECT_DEF`
  - `SAMPLEDB_TESTING_LDAP_LOGIN`
  - `SAMPLEDB_TESTING_LDAP_PW`
- If you introduce or significantly improve a feature, please document it and add it to the changelog in `docs/changelog.rst`. You can build the documentation using `python -m sphinx docs/ build/`.
- Once you are done, push your changes to your fork of SampleDB and open up a [pull request](https://github.com/sciapp/sampledb/compare).
