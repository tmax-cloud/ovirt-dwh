#!/usr/bin/python -E
'''
provides an installer for ovirt-engine-dwh
'''

"""
TODO:
1. refactor shared code with ovirt-reports-setup to common_utils
2. check DB connectivity
3. enable command line options
4. refactor to share same UI as ovirt engine setup
"""

import logging
import sys
import os
import time
import traceback
import pwd
import argparse
import getpass
import types
import cracklib
import common_utils as utils
from decorators import transactionDisplay
log_file = None

DWH_PACKAGE_NAME="ovirt-engine-dwh"
PATH_DB_SCRIPTS="/usr/share/ovirt-engine-dwh/db-scripts"
PATH_WATCHDOG="/usr/share/ovirt-engine-dwh/etl/ovirt_engine_dwh_watchdog.cron"
EXEC_CREATE_SCHEMA="create_schema.sh"
EXEC_UPGRADE_DB="upgrade.sh"
FILE_DB_CONN = "/etc/ovirt-engine/ovirt-engine-dwh/Default.properties"
FILE_ENGINE_CONF_DEFAULTS = "/usr/share/ovirt-engine/conf/engine.conf.defaults"
FILE_ENGINE_CONF = "/etc/ovirt-engine/engine.conf"
FILE_DATABASE_CONFIG = "/etc/ovirt-engine/engine.conf.d/10-setup-database.conf"
DIR_DWH_CONFIG = "/etc/ovirt-engine-dwh"
DIR_DATABASE_DWH_CONFIG = os.path.join(
    DIR_DWH_CONFIG,
    'engine-dwh.conf.d',
)
FILE_DATABASE_DWH_CONFIG = os.path.join(
    DIR_DATABASE_DWH_CONFIG,
    '10-setup-database-dwh.conf'
)
DB_BACKUPS_DIR = "/var/lib/ovirt-engine/backups"
DB_NAME = "ovirt_engine_history"
DB_USER = 'engine_history'
DB_PORT = "5432"
DB_HOST = "localhost"
PGPASS_TEMP = ''

# DB messages
DB_FILE = (
    "The DB was backed up as '{dbfile}'"
)
DB_RESTORE = (
    'The DB backup was created with compression. You must use "pg_restore" '
    'command if you need to recover the DB from the backup.'
)

#TODO: Create output messages file with all messages
#TODO: Move all errors here to make consistent usage
# ERRORS:
ERR_DB_CREATE_FAILED = "Error while trying to create %s db" % DB_NAME

def _verifyUserPermissions():
    username = pwd.getpwuid(os.getuid())[0]
    if os.geteuid() != 0:
        sys.exit(
            'Error: insufficient permissions for user {user}, '
            'you must run with user root.'.format(
                user=username
            )
        )

def dbExists(db_dict):
    logging.debug("checking if %s db already exists" % db_dict['dbname'])
    (output, rc) = utils.execSqlCmd(
        db_dict=db_dict,
        sql_query="select 1",
        envDict={'ENGINE_PGPASS': PGPASS_TEMP},
    )
    if (rc != 0):
        return False
    else:
        return True

@transactionDisplay("Creating DB")
def createDbSchema(db_dict):
    """
    create fresh ovirt_engine_history db
    """
    logging.debug("installing %s db", DB_NAME)

    dbLogFilename = "ovirt-history-db-install-%s.log" %(utils.getCurrentDateTime())
    logging.debug("ovirt engine history db creation is logged at %s/%s" % ("/var/log/ovirt-engine", dbLogFilename))

    cmd = [
        os.path.join(PATH_DB_SCRIPTS, EXEC_CREATE_SCHEMA),
        '-l', dbLogFilename,
        '-u', db_dict['username'],
        '-s', db_dict['host'],
        '-p', db_dict['port'],
    ]

    # Create db using shell command
    output, rc = utils.execCmd(
        cmdList=cmd,
        failOnError=True,
        msg=ERR_DB_CREATE_FAILED,
        envDict={'ENGINE_PGPASS': PGPASS_TEMP},
    )
    logging.debug('Successfully installed %s DB' % db_dict['dbname'])


@transactionDisplay("Upgrade DB")
def upgradeDB(db_dict):
    """
    upgrade existing ovirt_engine_history db
    """
    logging.debug("upgrading %s db", DB_NAME)
    dbLogFilename = "ovirt-history-db-upgrade-%s.log" %(utils.getCurrentDateTime())
    logging.debug("ovirt engine history db upgrade is logged at %s/%s" % ("/var/log/ovirt-engine", dbLogFilename))

    # Try/Except so we'll be able to return to our current directory
    currDir = os.getcwd()
    try:
        cmd = [
            os.path.join(PATH_DB_SCRIPTS, EXEC_UPGRADE_DB),
            "-s", db_dict["host"],
            "-p", db_dict["port"],
            "-u", db_dict["username"],
            "-d", db_dict['dbname'],
            "-l", "/var/log/ovirt-engine/%s" % dbLogFilename,
        ]
        os.chdir(PATH_DB_SCRIPTS)
        output, rc = utils.execCmd(
            cmdList=cmd,
            failOnError=True,
            msg="Error while trying to upgrade %s DB" % DB_NAME,
            envDict={'ENGINE_PGPASS': PGPASS_TEMP},
        )
    except:
        os.chdir(currDir)
        raise

def getPassFromUser(string):
    """
    get a single password from the user
    """
    userInput = getpass.getpass(string)
    if type(userInput) != types.StringType or len(userInput) == 0:
        print "Cannot accept an empty password"
        return getPassFromUser(string)

    try:
        cracklib.FascistCheck(userInput)
    except:
        print "Warning: Weak Password."

    return userInput

def getDbCredentials(
    hostdefault='',
    portdefault='',
    userdefault='',
):
    """
    get db params from user
    """
    print (
        'Remote installation selected. Make sure that DBA creates a user '
        'and the database in the following fashion:\n'
        '\tcreate role <role> with login '
        'encrypted password <password>;\n'
        '\tcreate ovirt_engine_history owner <role>;\n'
    )

    dbhost = utils.askQuestion(
        question='Enter the host name for the DB server',
        default=hostdefault,
    )

    dbport = utils.askQuestion(
        question='Enter the port of the remote DB server',
        default=portdefault or '5432',
    )

    dbuser = utils.askQuestion(
        question='Provide a remote DB user',
        default=userdefault,
    )

    userInput = getPassFromUser(
        'Please choose a password for the db user: '
    )
    # We do not need verification for the re-entered password
    userInput2 = getpass.getpass("Re-type password: ")
    if userInput != userInput2:
            print "ERROR: passwords don't match"
            return getDbCredentials(dbhost, dbport, dbuser)

    return (dbhost, dbport, dbuser, userInput)

def getDbDictFromOptions():
    if os.path.exists(FILE_DATABASE_CONFIG):
        handler = utils.TextConfigFileHandler(FILE_DATABASE_CONFIG)
        handler.open()
        dhandler = handler
        if os.path.exists(FILE_DATABASE_DWH_CONFIG):
            dhandler = utils.TextConfigFileHandler(FILE_DATABASE_DWH_CONFIG)
            dhandler.open()
        db_dict = {
            'dbname': (
                dhandler.getParam('DWH_DATABASE') or
                DB_NAME
            ),
            'host': handler.getParam('ENGINE_DB_HOST'),
            'port': handler.getParam('ENGINE_DB_PORT'),
            'username': (
                dhandler.getParam('DWH_USER') or
                DB_USER
            ),
            'password': (
                dhandler.getParam('DWH_PASSWORD') or
                utils.generatePassword()
            ),
            'readonly': (
                dhandler.getParam('DWH_READONLY_USER') or
                None
            ),
            'engine_db': handler.getParam('ENGINE_DB_DATABASE'),
            'engine_user': handler.getParam('ENGINE_DB_USER'),
            'engine_pass': handler.getParam('ENGINE_DB_PASSWORD').replace('"', ''),
        }
        handler.close()
        dhandler.close()
    else:
        db_dict = {
            'dbname': DB_NAME,
            'host': utils.getDbHostName(),
            'port': utils.getDbPort(),
            'username': utils.getDbAdminUser(),
            'password': utils.getPassFromFile(utils.getDbAdminUser()),
            'readonly': None,
        }

    return db_dict


@transactionDisplay("Setting DB connectivity")
def setDbPass(db_dict):
    '''
    set the password for the user postgres
    '''
    logging.debug("Setting DB pass")
    logging.debug("editing etl db connectivity file")

    file_handler = utils.TextConfigFileHandler(FILE_DB_CONN)
    file_handler.open()
    file_handler.editParam("ovirtEngineHistoryDbPassword", db_dict["password"])
    file_handler.editParam("ovirtEngineHistoryDbUser", DB_USER)
    file_handler.editParam("ovirtEngineDbPassword", db_dict["engine_pass"])
    file_handler.editParam("ovirtEngineDbUser", db_dict["engine_user"])
    file_handler.editParam("ovirtEngineDbJdbcConnection",
                           "jdbc\:postgresql\://%s\:%s/engine?stringtype\=unspecified" % (db_dict["host"], db_dict["port"]))
    file_handler.editParam("ovirtEngineHistoryDbJdbcConnection",
                           "jdbc\:postgresql\://%s\:%s/%s?stringtype\=unspecified" % (db_dict["host"], db_dict["port"], db_dict['dbname']))
    file_handler.close()

def isVersionSupported(rawMinimalVersion, rawCurrentVersion):
    """
    Check installed version with minimal support version
    """
    # Get current rpm version and parse it.
    (currentVersion, currentMinorVersion, currentRelease) = utils.parseVersionString(rawCurrentVersion)

    # Since minimalETLversion in vdc_options does not contain the "-something" release in its string. we add
    # it in order not to break the parseVersionString interface
    (minimalVersion, minimalMinorVersion, minimalRelease) = utils.parseVersionString("%s-0" % rawMinimalVersion)

    if (float(currentVersion) != float(minimalVersion)) or (int(currentMinorVersion) < int(minimalMinorVersion)):
        return False
    return True

def setVersion():
    """
    set the etlVersion option to current version
    """
    versionString = utils.getAppVersion(DWH_PACKAGE_NAME)
    (currentVersion, currentMinorVersion, currentRelease) = utils.parseVersionString(versionString)
    logging.debug("Setting etlVersion")
    logging.debug("editing etl connectivity file")
    file_handler = utils.TextConfigFileHandler(FILE_DB_CONN)
    file_handler.open()
    file_handler.editParam("etlVersion", "%s.%s" % (currentVersion, currentMinorVersion))
    file_handler.close()

def main():
    '''
    main
    '''

    rc = 0
    doBackup = None
    backupFile = None
    pg_updated = False

    readonly_user = None
    readonly_pass = None
    readonly_secure = None

    global PGPASS_TEMP

    parser = argparse.ArgumentParser(description='Installs or upgrades your oVirt Engine DWH')
    # Catch when calling ovirt-engine-dwh-setup --help
    args = parser.parse_args()

    try:
        logging.debug("starting main()")
        print "Welcome to ovirt-engine-dwh setup utility\n"

        db_dict = getDbDictFromOptions()
        PGPASS_TEMP = utils.createTempPgpass(db_dict)
        for dwh_path in (
            DIR_DWH_CONFIG,
            DIR_DATABASE_DWH_CONFIG
        ):
            if not os.path.exists(dwh_path):
                os.makedirs(dwh_path)
                os.chmod(dwh_path, 0644)

        # Get minimal supported version from oVirt Engine
        minimalVersion = utils.getVDCOption(
            key="MinimalETLVersion",
            db_dict=db_dict,
            temp_pgpass=PGPASS_TEMP,
        )
        currentVersion = utils.getAppVersion(DWH_PACKAGE_NAME)
        if not isVersionSupported(minimalVersion, currentVersion):
            print "Minimal supported version (%s) is higher then installed version (%s), please update the %s package" % (minimalVersion, currentVersion, DWH_PACKAGE_NAME)
            raise Exception("current version not supported by ovirt engine")

        # Stop engine
        if utils.stopEngine():

            # Stop ETL before doing anything
            utils.stopEtl()

            setVersion()

            # Create/Upgrade DB
            if utils.localHost(db_dict['host']):
                pg_updated = utils.configHbaIdent()

                # Handle postgres configuration for the read-only user
                # on local installations only

                readUserCreated = False
                errMsg = ''
                if db_dict['readonly'] is None:
                    # Ask user how would the user be created
                    createReadUser = utils.askYesNo(
                        question=(
                            '\nThis utility can configure a read only user for DB access. '
                            'Would you like to do so?'
                        )
                    )

                    if not createReadUser:
                        logging.debug('Skipping creation of read only DB user.')
                        print 'Skipping creationg of read only DB user.'
                    else:
                        readonly_user = utils.askQuestion(
                            question='Provide a username for read-only user'
                        )
                        readonly_pass = getpass.getpass(
                            prompt='Provide a password for read-only user: '
                        )
                        readonly_secure = utils.askYesNo(
                            question=(
                                'Should postgresql be setup with secure connection?'
                            )
                        )

            # Save configuration to the conf.d file
            utils.saveConfig(
                configFile=FILE_DATABASE_DWH_CONFIG,
                username=db_dict['username'],
                password=db_dict['password'],
                dbname=db_dict['dbname'],
                readonly=db_dict['readonly'],
            )


            if dbExists(db_dict):
                try:
                    doBackup = utils.performBackup(db_dict, DB_BACKUPS_DIR, PGPASS_TEMP)
                    backupFile = os.path.join(
                        DB_BACKUPS_DIR,
                        'ovirt-engine-history.backup.{date}'.format(
                            date=utils.getCurrentDateTime(),
                        )
                    )
                    if doBackup:
                        utils.backupDB(
                            backupFile,
                            db_dict,
                            PGPASS_TEMP,
                        )
                except UserWarning:
                    print 'User decided to stop setup. Exiting.'
                    # Start Services
                    utils.startEngine()
                    # Sleep for 20 secs to allow health applet to start
                    time.sleep(20)
                    utils.startEtl()
                    sys.exit(0)

                # Backup went ok, so upgrade
                upgradeDB(db_dict)
            else:
                if utils.localHost(db_dict["host"]):
                    utils.createUser(
                        user=db_dict['username'],
                        password=db_dict['password'],
                        option='createdb',
                    )

                    utils.createDB(db_dict['dbname'], db_dict['username'])
                    utils.updatePgHba(db_dict['dbname'], db_dict['username'])
                    utils.restartPostgres()

                else:
                    print 'Remote installation is selected.\n'
                    (
                        db_dict['host'],
                        db_dict['port'],
                        db_dict['username'],
                        db_dict['password'],
                    ) = getDbCredentials()
                    if os.path.exists(PGPASS_TEMP):
                        os.remove(PGPASS_TEMP)
                    PGPASS_TEMP = utils.createTempPgpass(db_dict)
                    if not utils.dbExists(db_dict, PGPASS_TEMP):
                        raise RuntimeError (
                            (
                                'Remote installation failed. Please perform '
                                '\tcreate role {role} with login '
                                'encrypted password {password};\n'
                                '\tcreate {db} owner {role}\n'
                                'on the remote DB, verify it and rerun the setup.'
                            ).format(
                                role=db_dict['username'],
                                db=db_dict['dbname'],
                                password=db_dict['password'],
                            )
                        )

                createDbSchema(db_dict)

            if utils.localHost(db_dict["host"]):
                # Create read only
                readUserCreated, errMsg = utils.createReadOnlyUser(
                    db_dict['dbname'],
                    readonly_user,
                    readonly_pass,
                    readonly_secure,
                )

                if not readUserCreated:
                    print (
                        'While trying to create a read-only DB user, '
                        'the following error received: {error}'
                    ).format(
                        error=errMsg
                    )
                else:
                    db_dict['readonly'] = readonly_user

            # Set DB connecitivty (user/pass)
            if db_dict['password']:
                setDbPass(db_dict)

            # Start Services
            utils.startEngine()
            # Sleep for 20 secs to allow health applet to start
            time.sleep(20)
            utils.startEtl()

            print "Successfully installed %s." % DWH_PACKAGE_NAME
            print "The installation log file is available at: %s" % log_file
            if doBackup:
                print DB_FILE.format(
                    dbfile=backupFile
                )
                print DB_RESTORE

            utils.saveConfig(
                configFile=FILE_DATABASE_DWH_CONFIG,
                username=db_dict['username'],
                password=db_dict['password'],
                dbname=db_dict['dbname'],
                readonly=db_dict['readonly'],
            )

        else:
            logging.debug("user chose not to stop engine")
            print "Installation stopped, Goodbye."

        logging.debug("main() ended")
    except:
        logging.error("Exception caught!")
        logging.error(traceback.format_exc())
        print "Error encountered while installing %s, please consult the log file: %s" % (DWH_PACKAGE_NAME,log_file)
        rc = 1
    finally:
        if os.path.exists(PGPASS_TEMP):
            os.remove(PGPASS_TEMP)

        if pg_updated:
            utils.restorePgHba()

        return rc

if __name__ == "__main__":
    # Check permissions first
    _verifyUserPermissions()

    # Initiate logging
    log_file = utils.initLogging(
        "%s-setup" % DWH_PACKAGE_NAME,
        "/var/log/ovirt-engine"
    )

    rc = main()
    sys.exit(rc)
