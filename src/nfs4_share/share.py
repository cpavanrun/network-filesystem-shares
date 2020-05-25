import logging
import os
import sys

from . acl import AccessControlList, AccessControlEntity


class Share:
    """
    Share object that is represented by a directory on the filesystem.

    Non-python Dependencies
    ------------
    Only works when creating shares on a NFSv4 filesystem.

    Needs the following binaries:
    * `/usr/bin/nfs4_setfacl`
    * `/usr/bin/nfs4_getfacl`
    """
    def __init__(self, directory, exist_ok=False):
        self.directory = os.path.realpath(directory)
        if os.path.exists(directory):
            logging.debug("\'%s\' exists." % os.path.basename(directory))
        if os.path.exists(directory) and os.path.isfile(directory):
            raise IllegalShareSetupError("%s should be not non-existent or a directory!" % directory)
        os.makedirs(directory, exist_ok=exist_ok)
        lock_ace = AccessControlEntity(entry_type="D",
                                       flags="",
                                       identity="EVERYONE",
                                       domain='',
                                       permissions="wadNTo")
        self.lock_acl = AccessControlList([lock_ace])

    def __repr__(self):
        return "Share({!r})".format(os.path.basename(self.directory))

    @property
    def permissions(self):
        return AccessControlList.from_file(self.directory)

    @permissions.setter
    def permissions(self, acl):
        logging.debug("Setting permissions on %s: %s" % (self.directory, acl))
        acl.set(self.directory)

    def add(self, items):
        """
        Adds items to the share
        """
        logging.debug("Adding items to %s: %s" % (self.directory, items))
        files = [i for i in items if os.path.isfile(i)]
        directories = [i for i in items if os.path.isdir(i)]
        for file in files:
            shared_file = os.path.join(self.directory, os.path.basename(file))
            self._link_files(file, shared_file)
        for directory in directories:
            try:
                self._duplicate_as_linked_tree(directory)
            except FileExistsError as e:
                logging.debug("Directory %s already exists! Going to remove and re-add it!" % e.filename)
                # It is already there, either by having been added before or within an update
                self._unshare_linked_tree(e.filename)
                self._duplicate_as_linked_tree(directory)
        for unhandled_item in set(items) - set(directories).union(set(files)):
            logging.error("Did not handle input item '%s'" % unhandled_item)

    def _duplicate_as_linked_tree(self, source_root):
        """
        Traverses the directory tree, creating new directories but hard-linking files.
        """
        logging.debug("Started traversing %s \'s tree for file linkage and directory duplication." % self.directory)
        #  Create the containing directory that resides within the share
        within_share_dir_path = os.path.join(self.directory, os.path.basename(source_root))
        self._makedir(within_share_dir_path)
        for root, subdirectories, files in os.walk(source_root, followlinks=True):
            share_root = root.replace(source_root, within_share_dir_path, 1)
            for subdir in subdirectories:
                target = os.path.join(share_root, subdir)
                self._makedir(target)
            for file in files:
                source = os.path.join(root, file)
                target = os.path.join(share_root, file)
                self._link_files(source, target)

    def _unshare_linked_tree(self, directory, force_file_removal=False):
        """
        will have the share remove itself
        """
        logging.debug("Started traversing %s\'s tree from bottom up for un-sharing" % self.directory)
        for root, subdirectories, files in os.walk(directory, topdown=False, followlinks=True):
            for shared_file in files:
                self._unshare_file(os.path.join(root, shared_file), force=force_file_removal)
            for sub_dir in subdirectories:
                self._unshare_dir(os.path.join(root, sub_dir))
        os.rmdir(directory)

    def lock(self):
        """
        locks down the share for changing anything other than the access
        """
        self._lock(self.directory)

    def unlock(self):
        """
        unlocks down the share for changing anything other than the access
        """
        self._unlock(self.directory)

    def _lock(self, target):
        logging.debug("Locking %s recursively" % target)
        self.lock_acl.append(target=target, recursive=True)

    def _unlock(self, target):
        logging.debug("Unlocking %s recursively" % target)
        self.lock_acl.unset(target=target, recursive=True)

    def _makedir(self, directory):
        """
        Created a directory and outputs to log
        """
        logging.debug("Creating %s" % directory)
        os.makedirs(directory)
        self.permissions.set(target=directory)

    def _link_files(self, source, target):
        """
        Creates a hard link between two files and outputs to log
        """
        try:
            self._unlock(source)    # prevents locking from messing up the setting of permissions
            logging.debug("Linking %s and %s" % (source, target))
            os.link(os.path.realpath(source), target)
            self.permissions.append(target=target)
        except PermissionError as e:
            msg = "ERROR: Insufficient rights on {}! Possible cause; source file need to be writable/appendable when fs.protect_hardlinks is enabled. Permissions: {}"
            logging.error(msg.format(e.filename, str(AccessControlList.from_file(target))))
        except FileExistsError as e:
            logging.debug("File %s already exists! Going to remove and re-add it!" % e.filename)
            # It is already there, either by having been added before or within an update
            self._unshare_file(target)
            self._link_files(source, target)

    def self_destruct(self, force_file_removal=False):
        """
        will have the share remove itself
        """
        self._unshare_linked_tree(directory=self.directory, force_file_removal=force_file_removal)

    def _unshare_dir(self, target):
        """
        Removes a directory from this share, fails when directory is not empty
        """
        logging.debug("Un-sharing directory %s" % target)
        os.rmdir(target)

    def _unshare_file(self, target, force=False):
        """
        Removes a file from this share
        """
        logging.debug("Un-sharing file %s" % target)
        if not force and os.stat(target).st_nlink == 1:
            msg = "File %s has ONE hard link. Un-sharing this file will delete it! Apply \'--force\' to do so." % target
            logging.error(msg)
            raise FileNotFoundError(msg)
        new_permissions = AccessControlList.from_file(target) - self.permissions
        logging.debug("File %s new permissions: %s" % (target, new_permissions))
        new_permissions.set(target)
        os.unlink(target)


class IllegalShareSetupError(RuntimeError):
    def __init__(self, message):
        # Call the base class constructor with the parameters it needs
        super().__init__(message)
