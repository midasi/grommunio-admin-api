/*
 * SPDX-License-Identifier: AGPL-3.0-or-later
 * SPDX-FileCopyrightText: 2020-2021 grammm GmbH
 */
%module pyexmdb

%warnfilter(325) Folder;
%warnfilter(325) Owner;

%{
    #include "queries.h"
    #include "structures.h"

    namespace exmdbpp::structures
    {
        TaggedPropval TaggedPropval_u64(uint32_t tag, uint64_t value)
        {return TaggedPropval(tag, value);}

        TaggedPropval TaggedPropval_str(uint32_t tag, const std::string& value)
        {return TaggedPropval(tag, value);}
    }
%}

%include "std_string.i"
%include "std_except.i"
%include "std_vector.i"
%include "stdint.i"

%template(Vuint32_t) std::vector<uint32_t>;
%template(VTaggedPropval) std::vector<exmdbpp::structures::TaggedPropval>;
%template(VVTaggedPropval) std::vector<std::vector<exmdbpp::structures::TaggedPropval> >;
%template(VPropertyProblem) std::vector<exmdbpp::structures::PropertyProblem>;

namespace exmdbpp
{

class ExmdbError : public std::runtime_error
{
public:
    ExmdbError(const std::string&, uint8_t);

    const uint8_t code;
};

namespace structures
{

struct TaggedPropval
{
    TaggedPropval() = default;
    TaggedPropval(const TaggedPropval&);
    ~TaggedPropval();
    uint32_t tag;
    uint16_t type;
    std::string printValue() const;
    std::string toString() const;
};

struct PropertyProblem
{
    PropertyProblem() = default;

    uint16_t index;
    uint32_t proptag;
    uint32_t err;
};

TaggedPropval TaggedPropval_u64(uint32_t, uint64_t);
TaggedPropval TaggedPropval_str(uint32_t, const std::string&);

}

namespace requests
{

struct NullResponse
{
    NullResponse() = default;
};

template<uint8_t CallId>
struct Response : NullResponse
{
    Response() = default;
};

struct QueryTableRequest;
struct CreateFolderByPropertiesRequest;
struct DeleteFolderRequest;
struct DeleteFolderRequest;
struct SetStorePropertiesRequest;
struct GetFolderPropertiesRequest;

%nodefaultctor;

template<>
struct Response<exmdbpp::requests::QueryTableRequest::callId>
{
    std::vector<std::vector<structures::TaggedPropval> > entries;
};

template<>
struct Response<exmdbpp::requests::CreateFolderByPropertiesRequest::callId>
{
    uint64_t folderId;
};

struct PropvalResponse
{
    std::vector<structures::TaggedPropval> propvals;
};

struct SuccessResponse
{
    bool success;
};

struct ProblemsResponse
{
    std::vector<structures::PropertyProblem> problems; ///< List of problems that occured when setting store values
};


%clearnodefaultctor;

%template(QueryTableResponse) requests::Response<exmdbpp::requests::QueryTableRequest::callId>;
%template(CreateFolderByPropertiesResponse) requests::Response<exmdbpp::requests::CreateFolderByPropertiesRequest::callId>;

}

namespace queries
{

struct Folder
{
    Folder() = default;
    Folder(const std::vector<structures::TaggedPropval>&);
    Folder(const requests::PropvalResponse&);

    uint64_t folderId;
    std::string displayName;
    std::string comment;
    uint64_t creationTime;
    std::string container;
};

struct FolderListResponse
{
    FolderListResponse(const requests::Response<requests::QueryTableRequest::callId>&);

    std::vector<queries::Folder> folders;
};

struct Owner
{
    uint64_t memberId;
    std::string memberName;
    uint32_t memberRights;
};

struct FolderOwnerListResponse
{
    struct Owner
    {
        uint64_t memberId;
        std::string memberName;
        uint32_t memberRights;
    };

    FolderOwnerListResponse(const requests::Response<requests::QueryTableRequest::callId>&);

    std::vector<queries::Owner> owners;
};

class ExmdbQueries
{
public:
    ExmdbQueries(const std::string& host, const std::string& port, const std::string& prefix, bool isPrivate) throw (ExmdbError, std::runtime_error);

    static const std::vector<uint32_t> defaultFolderProps;

    requests::NullResponse addFolderOwner(const std::string& homedir, uint64_t folderID, const std::string& username) throw (ExmdbError, std::runtime_error, std::out_of_range);
    requests::Response<requests::CreateFolderByPropertiesRequest::callId> createFolder(const std::string& homedir, uint32_t domainID, const std::string& folderName, const std::string& container, const std::string& comment) throw (ExmdbError, std::runtime_error, std::out_of_range);
    requests::SuccessResponse deleteFolder(const std::string& homedir, uint64_t folderID) throw (ExmdbError, std::runtime_error, std::out_of_range);
    requests::NullResponse deleteFolderOwner(const std::string& homedir, uint64_t folderID, uint64_t memberID) throw (ExmdbError, std::runtime_error, std::out_of_range);
    requests::Response<requests::QueryTableRequest::callId> getFolderList(const std::string& homedir, const std::vector<uint32_t>& proptags = defaultFolderProps) throw (ExmdbError, std::range_error, std::runtime_error, std::out_of_range);
    requests::Response<requests::QueryTableRequest::callId> getFolderOwnerList(const std::string& homedir, uint64_t folderID) throw (ExmdbError, std::runtime_error, std::out_of_range);
    requests::PropvalResponse getFolderProperties(const std::string& homedir, uint32_t cpid, uint64_t folderId, const std::vector<uint32_t>& proptags = defaultFolderProps) throw (ExmdbError, std::range_error, std::runtime_error, std::out_of_range);
    requests::PropvalResponse getStoreProperties(const std::string& homedir, uint32_t cpid, const std::vector<uint32_t>& proptags) throw (ExmdbError, std::range_error, std::runtime_error, std::out_of_range);
    requests::ProblemsResponse setFolderProperties(const std::string& homedir, uint32_t cpid, uint64_t folderId, const std::vector<structures::TaggedPropval>& propvals) throw (ExmdbError, std::range_error, std::runtime_error, std::out_of_range);
    requests::ProblemsResponse setStoreProperties(const std::string& homedir, uint32_t cpid, const std::vector<structures::TaggedPropval>& propvals) throw (ExmdbError, std::range_error, std::runtime_error, std::out_of_range);
    requests::NullResponse unloadStore(const std::string& homedir) throw (ExmdbError, std::runtime_error, std::out_of_range);
};

}

}

%{
namespace exmdbpp::queries
{
    typedef FolderOwnerListResponse::Owner Owner;
}
%}

%template(VFolder) std::vector<exmdbpp::queries::Folder>;
%template(VOwner) std::vector<exmdbpp::queries::Owner>;
